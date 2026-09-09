"""Phase 6 #t78 分类审计。

对标 JumpServer ``OperateLog`` / ``PasswordChangeLog`` / ``ActivityLog`` / ``UserSession``。
每条分类日志必须先写入 #t61 审计 hash chain，再以 ``audit_event_id`` 回指。
摘要、detail 与 metadata 不得包含密码、secret、token 或凭据正文。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OperateLog(Base):
    """一次管理面写操作的分类审计（创建/更新/删除资源）。"""

    __tablename__ = "operate_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_username: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    audit_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_operate_logs_tenant_occurred_id", "tenant_id", "occurred_at", "id"),
    )


class PasswordChangeLog(Base):
    """一次成功改密的分类审计；不保存新旧密码。"""

    __tablename__ = "password_change_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="self")
    audit_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_password_change_logs_tenant_occurred_id",
            "tenant_id",
            "occurred_at",
            "id",
        ),
    )


class ActivityLog(Base):
    """资源时间线活动，不含凭据正文。"""

    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_username: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    audit_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_activity_logs_tenant_occurred_id", "tenant_id", "occurred_at", "id"),
        Index("ix_activity_logs_tenant_resource", "tenant_id", "resource_type", "resource_id"),
    )


class OnlineUserSession(Base):
    """交互式登录在线会话；不含 access/refresh token。"""

    __tablename__ = "online_user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    client_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    audit_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ended_audit_event_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_online_user_sessions_tenant_status_id", "tenant_id", "status", "id"),
    )
