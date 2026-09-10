"""Phase 4 webhook endpoint management API routes; #t75 扩展 channel_type。"""
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    NotificationChannelType,
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
    INBOX_SENTINEL_URL,
    public_channel_url,
    validate_channel_endpoint,
)

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
    channel_type = data.channel_type.value
    url = INBOX_SENTINEL_URL if channel_type == NotificationChannelType.INBOX.value else data.url
    _validate_channel_url(channel_type=channel_type, url=url)
    credential = (data.credential or "").strip()
    endpoint = WebhookEndpoint(
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        url=url,
        event_types_json=json.dumps(data.event_types),
        signing_secret_digest=_secret_digest(data.signing_secret),
        channel_type=channel_type,
        target=(data.target or "").strip(),
        credential_encrypted=encrypt_field(credential) if credential else "",
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


def _validate_channel_url(*, channel_type: str, url: str) -> None:
    try:
        validate_channel_endpoint(channel_type=channel_type, url=url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _secret_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _webhook_endpoint_response(endpoint: WebhookEndpoint) -> WebhookEndpointResponse:
    return WebhookEndpointResponse(
        id=endpoint.id,
        tenant_id=endpoint.tenant_id,
        name=endpoint.name,
        url=public_channel_url(endpoint.url),
        event_types=_event_types(endpoint.event_types_json),
        status=WebhookEndpointStatus(endpoint.status),
        channel_type=NotificationChannelType(endpoint.channel_type or "webhook"),
        target=endpoint.target or "",
        signing_secret_configured=endpoint.signing_secret_digest is not None,
        credential_configured=bool((endpoint.credential_encrypted or "").strip()),
        created_at=_as_utc(endpoint.created_at),
        updated_at=_as_utc(endpoint.updated_at),
    )


def _event_types(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
