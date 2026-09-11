"""系统消息订阅 API：把事件类型扇出到指定用户与通知渠道。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    NotificationRuleStatus,
    SystemMessageSubscriptionCreate,
    SystemMessageSubscriptionListResponse,
    SystemMessageSubscriptionResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.webhook import SystemMessageSubscription, WebhookEndpoint
from app.services.notification_channels import NotificationChannelType
from app.services.notification_payload import event_types

router = APIRouter(prefix="/notification-subscriptions", tags=["Notification Subscriptions"])


@router.get("/", response_model=SystemMessageSubscriptionListResponse)
async def list_subscriptions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMessageSubscriptionListResponse:
    _require_notification_permission(user, "notifications:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SystemMessageSubscription, WebhookEndpoint)
        .join(
            WebhookEndpoint,
            (WebhookEndpoint.id == SystemMessageSubscription.webhook_endpoint_id)
            & (WebhookEndpoint.tenant_id == SystemMessageSubscription.tenant_id),
        )
        .where(SystemMessageSubscription.tenant_id == tenant_id)
        .order_by(SystemMessageSubscription.id)
    )
    rows = result.all()
    items = [_subscription_response(sub, endpoint=endpoint) for sub, endpoint in rows]
    return SystemMessageSubscriptionListResponse(items=items, total=len(items))


@router.post("/", response_model=SystemMessageSubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    data: SystemMessageSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMessageSubscriptionResponse:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = await _get_active_channel(
        db=db, tenant_id=tenant_id, endpoint_id=data.webhook_endpoint_id
    )
    subscription = SystemMessageSubscription(
        tenant_id=tenant_id,
        name=data.name,
        user_id=data.user_id,
        event_types_json=json.dumps(data.event_types),
        webhook_endpoint_id=endpoint.id,
        status=data.status.value,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return _subscription_response(subscription, endpoint=endpoint)


def _require_notification_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _get_active_channel(
    *, db: AsyncSession, tenant_id: str, endpoint_id: int
) -> WebhookEndpoint:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
            WebhookEndpoint.status == "active",
        )
    )
    endpoint = result.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="WEBHOOK_ENDPOINT_NOT_FOUND")
    return endpoint


def _subscription_response(
    subscription: SystemMessageSubscription, *, endpoint: WebhookEndpoint
) -> SystemMessageSubscriptionResponse:
    return SystemMessageSubscriptionResponse(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        name=subscription.name,
        user_id=subscription.user_id,
        event_types=event_types(subscription.event_types_json),
        webhook_endpoint_id=subscription.webhook_endpoint_id,
        webhook_endpoint_name=endpoint.name,
        channel_type=NotificationChannelType(endpoint.channel_type),
        status=NotificationRuleStatus(subscription.status),
        created_at=_as_utc(subscription.created_at),
        updated_at=_as_utc(subscription.updated_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
