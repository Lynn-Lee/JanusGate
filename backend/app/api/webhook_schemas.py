"""Schemas for webhook endpoint management API."""
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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


class WebhookEndpointStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class WebhookEndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(default="", max_length=512)
    event_types: list[str] = Field(min_length=1)
    signing_secret: str | None = Field(default=None, min_length=16, max_length=256)
    channel_type: NotificationChannelType = NotificationChannelType.WEBHOOK
    credential: str | None = Field(default=None, max_length=512)
    recipient: str | None = Field(default=None, max_length=255)
    auth_username: str | None = Field(default=None, max_length=120)
    status: WebhookEndpointStatus = WebhookEndpointStatus.ACTIVE


class WebhookEndpointResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    url: str
    event_types: list[str]
    status: WebhookEndpointStatus
    channel_type: NotificationChannelType
    signing_secret_configured: bool
    credential_configured: bool
    recipient: str | None
    auth_username: str | None
    created_at: datetime | None
    updated_at: datetime | None


class WebhookEndpointListResponse(BaseModel):
    items: list[WebhookEndpointResponse]
    total: int


class NotificationRuleStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class NotificationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    event_types: list[str] = Field(min_length=1)
    webhook_endpoint_id: int
    status: NotificationRuleStatus = NotificationRuleStatus.ACTIVE


class NotificationRuleResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    event_types: list[str]
    webhook_endpoint_id: int
    webhook_endpoint_name: str
    status: NotificationRuleStatus
    created_at: datetime | None
    updated_at: datetime | None


class NotificationRuleListResponse(BaseModel):
    items: list[NotificationRuleResponse]
    total: int


class NotificationDeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class NotificationDeliveryCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationDeliveryResponse(BaseModel):
    id: int
    tenant_id: str
    notification_rule_id: int
    webhook_endpoint_id: int
    event_type: str
    status: NotificationDeliveryStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime | None
    updated_at: datetime | None


class NotificationDeliveryListResponse(BaseModel):
    items: list[NotificationDeliveryResponse]
    total: int


class NotificationEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    subject_user_id: str | None = Field(default=None, max_length=64)


class NotificationEventFanoutResponse(BaseModel):
    event_type: str
    enqueued: int
    delivery_ids: list[int]


class SystemMessageSubscriptionCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    webhook_endpoint_id: int
    user_id: str | None = Field(default=None, max_length=64)


class SystemMessageSubscriptionResponse(BaseModel):
    id: int
    tenant_id: str
    event_type: str
    webhook_endpoint_id: int
    user_id: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None


class SystemMessageSubscriptionListResponse(BaseModel):
    items: list[SystemMessageSubscriptionResponse]
    total: int


class InAppMessageResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str
    event_type: str
    title: str
    body: str
    delivery_id: int | None
    read_at: datetime | None
    created_at: datetime | None


class InAppMessageListResponse(BaseModel):
    items: list[InAppMessageResponse]
    total: int
