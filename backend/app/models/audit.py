"""Phase 6 #t61 审计事件持久化模型。

审计事件以 append-only 方式落库，配合 per-tenant 的 sequence_number + hash chain
实现不可抵赖。`UNIQUE(tenant_id, sequence_number)` 在数据库层强制同一租户内序号
唯一且连续，任何篡改/重放都会破坏链或触发唯一约束。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditEventModel(Base):
    """append-only 审计事件。属性 ``event_metadata`` 对应审计 Schema 的 ``metadata``
    （``metadata`` 是 SQLAlchemy declarative 保留名，故改名）。"""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_username: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    siem_delivery_status: Mapped[str] = mapped_column(String(20), nullable=False)
    siem_delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    siem_delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    siem_next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence_number", name="uq_audit_events_tenant_sequence"),
    )
