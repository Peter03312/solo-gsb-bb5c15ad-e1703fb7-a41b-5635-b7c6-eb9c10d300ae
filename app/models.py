from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint)

from .db import Base


class Session(Base):
    """一个演出场次的频点规划工作区。"""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, default="")
    frequencies = Column(JSON, nullable=False)        # 可用整数频点列表
    groups = Column(JSON, nullable=False, default=list)        # [{id,start,end}]
    interferences = Column(JSON, nullable=False, default=list)  # [{a,b,min_separation}]
    version = Column(Integer, nullable=False, default=0)  # 当前发布版号，0=未发布


class PlanVersion(Base):
    """已发布的频点分配版本，发布后不可变。"""

    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("session_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id"),
                        nullable=False, index=True)
    version = Column(Integer, nullable=False)
    assignments = Column(JSON, nullable=False)  # {组编号: 频点}
    locks = Column(JSON, nullable=False, default=dict)  # 发布时使用的锁定
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc))
