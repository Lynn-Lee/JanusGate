"""Schemas for webhook endpoint management API."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class WebhookEndpointStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class WebhookEndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=512)
    event_types: list[str] = Field(min_length=1)
    signing_secret: str | None = Field(default=None, min_length=16, max_length=256)
    status: WebhookEndpointStatus = WebhookEndpointStatus.ACTIVE


class WebhookEndpointResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    url: str
    event_types: list[str]
    status: WebhookEndpointStatus
    signing_secret_configured: bool
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
    webhook_endpoint_id: int | None = None
    channel_id: int | None = None
    status: NotificationRuleStatus = NotificationRuleStatus.ACTIVE

    @model_validator(mode="after")
    def exactly_one_sink(self) -> NotificationRuleCreate:
        has_webhook = self.webhook_endpoint_id is not None
        has_channel = self.channel_id is not None
        if has_webhook == has_channel:
            raise ValueError("NOTIFICATION_RULE_SINK_REQUIRED")
        return self


class NotificationRuleResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    event_types: list[str]
    webhook_endpoint_id: int | None
    webhook_endpoint_name: str | None
    channel_id: int | None
    channel_name: str | None
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
    webhook_endpoint_id: int | None
    channel_id: int | None
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
