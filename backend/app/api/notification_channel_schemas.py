"""#t75 notification channel / subscription / inbox API schemas."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.notification_channel import ALL_CHANNEL_TYPES, CHANNEL_INBOX


class NotificationChannelStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class NotificationChannelType(StrEnum):
    WEBHOOK = "webhook"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"
    LARK = "lark"
    WECOM = "wecom"
    SLACK = "slack"
    SMS = "sms"
    EMAIL = "email"
    INBOX = "inbox"


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    channel_type: NotificationChannelType
    event_types: list[str] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    status: NotificationChannelStatus = NotificationChannelStatus.ACTIVE

    @model_validator(mode="after")
    def validate_known_type(self) -> NotificationChannelCreate:
        if self.channel_type.value not in ALL_CHANNEL_TYPES:
            raise ValueError("CHANNEL_TYPE_UNKNOWN")
        return self


class NotificationChannelResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    channel_type: NotificationChannelType
    event_types: list[str]
    config: dict[str, Any]
    status: NotificationChannelStatus
    created_at: datetime | None
    updated_at: datetime | None


class NotificationChannelListResponse(BaseModel):
    items: list[NotificationChannelResponse]
    total: int


class SystemMsgSubscriptionUpdate(BaseModel):
    event_types: list[str] = Field(min_length=1)
    channel_types: list[str] = Field(default_factory=lambda: [CHANNEL_INBOX])
    status: NotificationChannelStatus = NotificationChannelStatus.ACTIVE

    @model_validator(mode="after")
    def validate_channel_types(self) -> SystemMsgSubscriptionUpdate:
        if not self.channel_types:
            raise ValueError("CHANNEL_TYPES_REQUIRED")
        unknown = [item for item in self.channel_types if item not in ALL_CHANNEL_TYPES]
        if unknown:
            raise ValueError("CHANNEL_TYPE_UNKNOWN")
        return self


class SystemMsgSubscriptionResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str
    event_types: list[str]
    channel_types: list[str]
    status: NotificationChannelStatus
    created_at: datetime | None
    updated_at: datetime | None


class InboxMessageResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str
    event_type: str
    title: str
    body: dict[str, Any]
    read_at: datetime | None
    created_at: datetime | None


class InboxMessageListResponse(BaseModel):
    items: list[InboxMessageResponse]
    total: int
