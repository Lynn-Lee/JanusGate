"""Schemas for webhook endpoint management API."""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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
