"""Phase 6 #t62 会话网关状态持久化模型。

会话生命周期状态从进程内 `dict` 落库，多副本部署下会话本身可水平扩展、重启不丢。
与 #t46 `SessionRecording`（录制元数据）通过 `session_id` 逻辑关联：本表 `id` 即网关
会话 id，录制表 `session_recordings.session_id` 引用同一值（不设硬 FK，录制可独立创建）。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    connection_token_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    connector_session_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    connection_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    client_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    client_ip_source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    workflow_request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    jit_grant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audit_event_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index("ix_sessions_tenant_subject_created", "tenant_id", "subject_id", "created_at"),
    )
