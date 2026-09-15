"""Phase 4 webhook / Phase 6 #t75 通知渠道、订阅与站内信模型。"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WebhookEndpoint(Base):
    """租户通知渠道。`channel_type` 区分通用 WebHook、IM、HTTPS 网关与站内信。"""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_webhook_endpoints_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False, default="webhook")
    event_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    signing_secret_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    credential_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationRule(Base):
    """事件 → 渠道绑定。禁止绑定 inbox，站内信只走订阅。"""

    __tablename__ = "notification_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_notification_rules_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    webhook_endpoint_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationDelivery(Base):
    """可靠投递队列。payload 入库前已脱敏；响应永不回显。"""

    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    notification_rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    webhook_endpoint_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    recipient_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemMessageSubscription(Base):
    """系统消息订阅：按事件类型扇出到指定渠道；inbox 必须指定接收人。"""

    __tablename__ = "system_message_subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_system_message_subscriptions_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    webhook_endpoint_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    recipient_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InAppMessage(Base):
    """站内信。worker 写入当前租户指定接收人；列表只返回当前用户。"""

    __tablename__ = "in_app_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recipient_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    delivery_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
