"""#t75 notification channels, system-message subscriptions, and inbox."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

CHANNEL_WEBHOOK = "webhook"
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_FEISHU = "feishu"
CHANNEL_LARK = "lark"
CHANNEL_WECOM = "wecom"
CHANNEL_SLACK = "slack"
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_INBOX = "inbox"

HTTPS_IM_CHANNEL_TYPES = frozenset(
    {
        CHANNEL_DINGTALK,
        CHANNEL_FEISHU,
        CHANNEL_LARK,
        CHANNEL_WECOM,
        CHANNEL_SLACK,
    }
)
IMPLEMENTED_CHANNEL_TYPES = frozenset(
    {CHANNEL_WEBHOOK, *HTTPS_IM_CHANNEL_TYPES, CHANNEL_INBOX}
)
STUB_CHANNEL_TYPES = frozenset({CHANNEL_SMS, CHANNEL_EMAIL})
ALL_CHANNEL_TYPES = frozenset({*IMPLEMENTED_CHANNEL_TYPES, *STUB_CHANNEL_TYPES})


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_notification_channels_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemMsgSubscription(Base):
    __tablename__ = "system_msg_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", name="uq_system_msg_subscriptions_tenant_user"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    channel_types_json: Mapped[str] = mapped_column(Text, nullable=False, default='["inbox"]')
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
