"""Phase 6 #t78 分类审计：操作日志与改密日志。

对标 JumpServer ``OperateLog`` / ``PasswordChangeLog``。每条分类日志必须先写入
#t61 审计 hash chain，再以 ``audit_event_id`` 回指。摘要与 metadata 不得包含
密码、secret、token 或凭据正文。
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
