"""Phase 4/6 通知渠道（WebHook / IM / SMS / 邮件 / 站内信）管理 API。"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    WebhookEndpointCreate,
    WebhookEndpointListResponse,
    WebhookEndpointResponse,
    WebhookEndpointStatus,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.core.security import encrypt_field
from app.models.webhook import WebhookEndpoint
from app.services.notification_channels import (
    NotificationChannelType,
    sanitize_channel_url,
    validate_channel_url,
)
from app.services.notification_payload import event_types

router = APIRouter(prefix="/webhook-endpoints", tags=["Webhook"])


@router.get("/", response_model=WebhookEndpointListResponse)
async def list_webhook_endpoints(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> WebhookEndpointListResponse:
    _require_webhook_permission(user, "webhooks:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.tenant_id == tenant_id)
        .order_by(WebhookEndpoint.id)
    )
    endpoints = result.scalars().all()
    items = [_webhook_endpoint_response(endpoint) for endpoint in endpoints]
    return WebhookEndpointListResponse(items=items, total=len(items))


@router.post("/", response_model=WebhookEndpointResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook_endpoint(
    data: WebhookEndpointCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> WebhookEndpointResponse:
    _require_webhook_permission(user, "webhooks:write")
    url = validate_channel_url(channel_type=data.channel_type, url=data.url)
    credential_encrypted = encrypt_field(data.credential) if data.credential else None
    endpoint = WebhookEndpoint(
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        url=url,
        event_types_json=json.dumps(data.event_types),
        signing_secret_digest=_secret_digest(data.signing_secret),
        channel_type=data.channel_type.value,
        credential_encrypted=credential_encrypted,
        config_json=json.dumps(data.config, sort_keys=True, default=str),
        status=data.status.value,
    )
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return _webhook_endpoint_response(endpoint)


def _require_webhook_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _secret_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _webhook_endpoint_response(endpoint: WebhookEndpoint) -> WebhookEndpointResponse:
    channel_type = NotificationChannelType(endpoint.channel_type or NotificationChannelType.WEBHOOK)
    return WebhookEndpointResponse(
        id=endpoint.id,
        tenant_id=endpoint.tenant_id,
        name=endpoint.name,
        url=sanitize_channel_url(endpoint.url),
        event_types=event_types(endpoint.event_types_json),
        channel_type=channel_type,
        status=WebhookEndpointStatus(endpoint.status),
        signing_secret_configured=endpoint.signing_secret_digest is not None,
        credential_configured=bool(endpoint.credential_encrypted),
        created_at=_as_utc(endpoint.created_at),
        updated_at=_as_utc(endpoint.updated_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
