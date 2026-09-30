from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from . import solver
from .db import Base, SessionLocal, engine
from .models import PlanVersion, Session
from .schemas import (GroupsReplace, InterferencesReplace, PublishIn,
                      SessionCreate)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 等待数据库就绪并初始化表结构
    last_err: Exception | None = None
    for _ in range(60):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception as exc:  # 数据库尚未就绪
            last_err = exc
            time.sleep(1)
    else:
        raise RuntimeError(f"数据库连接失败: {last_err}")
    yield


app = FastAPI(title="演出无线频点协调服务", version="1.0.0", lifespan=lifespan)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_session(db: OrmSession, sid: int) -> Session:
    sess = db.get(Session, sid)
    if sess is None:
        raise HTTPException(404, f"场次 {sid} 不存在")
    return sess


def _session_view(sess: Session) -> dict:
    return {
        "id": sess.id,
        "name": sess.name,
        "version": sess.version,
        "frequencies": sess.frequencies,
        "groups": sess.groups,
        "interferences": sess.interferences,
    }


# ---------------------------------------------------------------- 场次

@app.post("/sessions", status_code=201)
def create_session(body: SessionCreate, db: OrmSession = Depends(get_db)):
    """创建场次，录入可用整数频点。"""
    if not body.frequencies:
        raise HTTPException(400, "可用频点列表不能为空")
    if len(set(body.frequencies)) != len(body.frequencies):
        raise HTTPException(400, "可用频点列表存在重复值")
    sess = Session(name=body.name,
                   frequencies=sorted(set(body.frequencies)),
                   groups=[], interferences=[], version=0)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return _session_view(sess)


@app.get("/sessions")
def list_sessions(db: OrmSession = Depends(get_db)):
    rows = db.execute(select(Session).order_by(Session.id)).scalars().all()
    return [_session_view(s) for s in rows]


@app.get("/sessions/{sid}")
def get_session(sid: int, db: OrmSession = Depends(get_db)):
    return _session_view(_get_session(db, sid))


# ---------------------------------------------------------------- 设备组

@app.put("/sessions/{sid}/groups")
def put_groups(sid: int, body: GroupsReplace, db: OrmSession = Depends(get_db)):
    """整体替换设备组及其使用时段（左闭右开）。任一非法则整次拒绝。"""
    sess = _get_session(db, sid)
    ids = [g.id for g in body.groups]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "设备组编号重复")
    for g in body.groups:
        if g.start >= g.end:
            raise HTTPException(
                400, f"设备组 {g.id} 时段非法：起点不早于终点 "
                     f"(start={g.start}, end={g.end})")
    new_ids = set(ids)
    dangling = [r for r in sess.interferences
                if r["a"] not in new_ids or r["b"] not in new_ids]
    if dangling:
        raise HTTPException(400, {
            "message": "更新后以下干扰关系将引用未知设备组，请先调整干扰关系",
            "interferences": dangling,
        })
    sess.groups = [g.model_dump() for g in body.groups]
    db.commit()
    return _session_view(sess)


# ---------------------------------------------------------------- 干扰关系

@app.put("/sessions/{sid}/interferences")
def put_interferences(sid: int, body: InterferencesReplace,
                      db: OrmSession = Depends(get_db)):
    """整体替换干扰关系。引用未知组或间距非正整数则整次拒绝。"""
    sess = _get_session(db, sid)
    group_ids = {g["id"] for g in sess.groups}
    for r in body.interferences:
        if r.a == r.b:
            raise HTTPException(400, f"干扰关系两端不能是同一设备组 {r.a}")
        unknown = [g for g in (r.a, r.b) if g not in group_ids]
        if unknown:
            raise HTTPException(
                400, f"干扰关系引用了未知设备组: {', '.join(unknown)}")
        if r.min_separation < 1:
            raise HTTPException(
                400, f"干扰关系 {r.a}-{r.b} 的最小频点间距必须为正整数")
    sess.interferences = [r.model_dump() for r in body.interferences]
    db.commit()
    return _session_view(sess)


# ---------------------------------------------------------------- 发布

@app.post("/sessions/{sid}/publish", status_code=201)
def publish(sid: int, body: PublishIn, db: OrmSession = Depends(get_db)):
    """求解并发布新版本。须提交所见版本号，过期版本返回 409。"""
    # 对场次行加写锁：并发发布在此串行化，只有基于最新版本的请求能成功
    sess = db.execute(
        select(Session).where(Session.id == sid).with_for_update()
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(404, f"场次 {sid} 不存在")
    if body.base_version != sess.version:
        raise HTTPException(409, {
            "message": "版本冲突：所提交的基础版本已过期，请重新获取当前版本",
            "current_version": sess.version,
        })

    groups = {g["id"]: (g["start"], g["end"]) for g in sess.groups}
    freqs = sorted(set(sess.frequencies))
    for gid, f in body.locks.items():
        if gid not in groups:
            raise HTTPException(400, f"锁定引用了未知设备组 {gid}")
        if f not in freqs:
            raise HTTPException(
                400, f"设备组 {gid} 锁定的频点 {f} 不在可用频点列表中")

    previous = {}
    if sess.version > 0:
        prev_row = db.execute(
            select(PlanVersion).where(
                PlanVersion.session_id == sid,
                PlanVersion.version == sess.version)
        ).scalar_one()
        previous = prev_row.assignments

    relations = [(r["a"], r["b"], r["min_separation"])
                 for r in sess.interferences]
    assignment = solver.solve(groups, freqs, relations, body.locks, previous)
    if assignment is None:
        core, locked_involved = solver.explain(groups, freqs, relations,
                                               body.locks)
        raise HTTPException(422, {
            "message": "无解：锁定与干扰关系约束冲突，当前发布版保持不变",
            "locked_groups": locked_involved,
            "interferences": [
                {"a": a, "b": b, "min_separation": s} for a, b, s in core],
        })

    new_version = sess.version + 1
    db.add(PlanVersion(session_id=sid, version=new_version,
                       assignments=assignment, locks=body.locks))
    sess.version = new_version
    db.commit()
    changes = sum(1 for g, f in assignment.items()
                  if g in previous and previous[g] != f)
    return {"session_id": sid, "version": new_version,
            "assignments": assignment, "changes": changes}


# ---------------------------------------------------------------- 版本查询

def _get_version(db: OrmSession, sid: int, version: int) -> PlanVersion:
    row = db.execute(
        select(PlanVersion).where(PlanVersion.session_id == sid,
                                  PlanVersion.version == version)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"场次 {sid} 的版本 {version} 不存在")
    return row


@app.get("/sessions/{sid}/versions")
def list_versions(sid: int, db: OrmSession = Depends(get_db)):
    _get_session(db, sid)
    rows = db.execute(
        select(PlanVersion).where(PlanVersion.session_id == sid)
        .order_by(PlanVersion.version)
    ).scalars().all()
    return [{"version": r.version, "created_at": r.created_at} for r in rows]


@app.get("/sessions/{sid}/versions/{version}")
def get_version(sid: int, version: int, db: OrmSession = Depends(get_db)):
    row = _get_version(db, sid, version)
    return {"session_id": sid, "version": row.version,
            "assignments": row.assignments, "locks": row.locks,
            "created_at": row.created_at}


@app.get("/sessions/{sid}/versions/{version}/diff")
def version_diff(sid: int, version: int, db: OrmSession = Depends(get_db)):
    """该版本与前一发布版的差异。"""
    cur = _get_version(db, sid, version)
    prev_assignments = {}
    if version > 1:
        prev = _get_version(db, sid, version - 1)
        prev_assignments = prev.assignments
    cur_assignments = cur.assignments
    changed = {g: {"from": prev_assignments[g], "to": f}
               for g, f in cur_assignments.items()
               if g in prev_assignments and prev_assignments[g] != f}
    added = {g: f for g, f in cur_assignments.items()
             if g not in prev_assignments}
    removed = {g: f for g, f in prev_assignments.items()
               if g not in cur_assignments}
    unchanged = sorted(g for g, f in cur_assignments.items()
                       if g in prev_assignments and prev_assignments[g] == f)
    return {"session_id": sid,
            "from_version": version - 1, "to_version": version,
            "changed": changed, "added": added, "removed": removed,
            "unchanged": unchanged}
