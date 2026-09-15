"""通知渠道、规则、投递队列、系统消息订阅与站内信。"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WebhookEndpoint(Base):
    """租户内通知渠道。通用 WebHook 与 IM/邮件/短信/站内信共用此表。

    ``url`` 对站内信可为空；IM 机器人 token 不保存在 URL 中，而是写入
    ``credential_encrypted``。响应层不得回显凭据明文。
    """

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_webhook_endpoints_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
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
    """把事件类型绑定到出站渠道。站内信必须走系统消息订阅，不能绑规则。"""

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
    """可靠投递队列记录。payload 入库前已脱敏；失败走重试与死信。

    规则投递填写 ``notification_rule_id``；订阅扇出填写 ``subscription_id``。
    二者至少其一，站内信还必须带 ``recipient_user_id``。
    """

    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    notification_rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    subscription_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
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
    """系统消息订阅：按事件类型扇出到指定渠道。站内信必须带接收人。"""

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
    """当前用户可见的站内信。正文已脱敏，跨用户、跨租户不可见。"""

    __tablename__ = "in_app_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    delivery_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
