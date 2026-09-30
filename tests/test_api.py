import os
import sys

os.environ["DATABASE_URL"] = "sqlite:////tmp/freqcoord_test.db"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    from app.db import Base, engine
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from app.main import app
    with TestClient(app) as c:
        yield c


def make_session(client, freqs=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)):
    r = client.post("/sessions", json={"name": "晚会", "frequencies": list(freqs)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def setup_basic(client, sid):
    r = client.put(f"/sessions/{sid}/groups", json={"groups": [
        {"id": "A", "start": 0, "end": 60},
        {"id": "B", "start": 30, "end": 90},
        {"id": "C", "start": 120, "end": 150},
    ]})
    assert r.status_code == 200, r.text
    r = client.put(f"/sessions/{sid}/interferences", json={"interferences": [
        {"a": "A", "b": "B", "min_separation": 3},
        {"a": "A", "b": "C", "min_separation": 2},
    ]})
    assert r.status_code == 200, r.text


def test_full_flow(client):
    sid = make_session(client)
    setup_basic(client, sid)

    # A-B 时段相交须满足间距 3；A-C 不相交，无约束
    r = client.post(f"/sessions/{sid}/publish",
                    json={"base_version": 0, "locks": {"A": 5}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["changes"] == 0  # 首次规划计零
    a = body["assignments"]
    assert a["A"] == 5
    assert abs(a["A"] - a["B"]) >= 3

    # 版本不可变，可读取
    r = client.get(f"/sessions/{sid}/versions/1")
    assert r.status_code == 200
    assert r.json()["assignments"] == a
    assert r.json()["locks"] == {"A": 5}

    # 差异：相对第 0 版（空）全部为新增
    r = client.get(f"/sessions/{sid}/versions/1/diff")
    d = r.json()
    assert d["from_version"] == 0 and d["to_version"] == 1
    assert d["added"] == a and d["changed"] == {} and d["removed"] == {}

    # 第二次发布：旧方案仍可行，应保持不变（改频数 0）
    r = client.post(f"/sessions/{sid}/publish",
                    json={"base_version": 1, "locks": {}})
    assert r.status_code == 201
    body2 = r.json()
    assert body2["version"] == 2
    assert body2["changes"] == 0
    assert body2["assignments"] == a

    r = client.get(f"/sessions/{sid}/versions/2/diff")
    d = r.json()
    assert d["changed"] == {} and d["added"] == {}
    assert sorted(d["unchanged"]) == ["A", "B", "C"]


def test_version_conflict(client):
    sid = make_session(client)
    setup_basic(client, sid)
    assert client.post(f"/sessions/{sid}/publish",
                       json={"base_version": 0}).status_code == 201
    # 旧版本号再次发布 → 409
    r = client.post(f"/sessions/{sid}/publish", json={"base_version": 0})
    assert r.status_code == 409
    assert r.json()["detail"]["current_version"] == 1


def test_invalid_interval_rejected(client):
    sid = make_session(client)
    r = client.put(f"/sessions/{sid}/groups", json={"groups": [
        {"id": "A", "start": 10, "end": 10},
        {"id": "B", "start": 0, "end": 5},
    ]})
    assert r.status_code == 400
    # 整次拒绝：B 也未写入
    assert client.get(f"/sessions/{sid}").json()["groups"] == []


def test_unknown_group_in_interference_rejected(client):
    sid = make_session(client)
    client.put(f"/sessions/{sid}/groups",
               json={"groups": [{"id": "A", "start": 0, "end": 10}]})
    r = client.put(f"/sessions/{sid}/interferences", json={"interferences": [
        {"a": "A", "b": "ZZ", "min_separation": 2}]})
    assert r.status_code == 400
    assert client.get(f"/sessions/{sid}").json()["interferences"] == []


def test_non_positive_separation_rejected(client):
    sid = make_session(client)
    client.put(f"/sessions/{sid}/groups", json={"groups": [
        {"id": "A", "start": 0, "end": 10},
        {"id": "B", "start": 0, "end": 10}]})
    r = client.put(f"/sessions/{sid}/interferences", json={"interferences": [
        {"a": "A", "b": "B", "min_separation": 0}]})
    assert r.status_code == 400


def test_lock_unknown_group_rejected(client):
    sid = make_session(client)
    setup_basic(client, sid)
    r = client.post(f"/sessions/{sid}/publish",
                    json={"base_version": 0, "locks": {"ZZ": 3}})
    assert r.status_code == 400


def test_lock_unavailable_frequency_rejected(client):
    sid = make_session(client)
    setup_basic(client, sid)
    r = client.post(f"/sessions/{sid}/publish",
                    json={"base_version": 0, "locks": {"A": 99}})
    assert r.status_code == 400


def test_infeasible_publish_keeps_version(client):
    sid = make_session(client, freqs=(1, 2, 3, 4, 5))
    client.put(f"/sessions/{sid}/groups", json={"groups": [
        {"id": "A", "start": 0, "end": 10},
        {"id": "B", "start": 0, "end": 10}]})
    client.put(f"/sessions/{sid}/interferences", json={"interferences": [
        {"a": "A", "b": "B", "min_separation": 5}]})
    r = client.post(f"/sessions/{sid}/publish",
                    json={"base_version": 0, "locks": {"A": 1}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["locked_groups"] == ["A"]
    assert detail["interferences"] == [
        {"a": "A", "b": "B", "min_separation": 5}]
    # 当前发布版不变
    assert client.get(f"/sessions/{sid}").json()["version"] == 0


def test_versions_immutable(client):
    sid = make_session(client)
    setup_basic(client, sid)
    r1 = client.post(f"/sessions/{sid}/publish", json={"base_version": 0})
    a1 = r1.json()["assignments"]
    # 收紧约束后再次发布，旧版本内容不变
    client.put(f"/sessions/{sid}/interferences", json={"interferences": [
        {"a": "A", "b": "B", "min_separation": 6}]})
    r2 = client.post(f"/sessions/{sid}/publish", json={"base_version": 1})
    assert r2.status_code == 201
    assert client.get(f"/sessions/{sid}/versions/1").json()["assignments"] == a1
    d = client.get(f"/sessions/{sid}/versions/2/diff").json()
    assert d["to_version"] == 2


def test_unknown_session_and_version(client):
    assert client.get("/sessions/999").status_code == 404
    sid = make_session(client)
    assert client.get(f"/sessions/{sid}/versions/3").status_code == 404
