"""#t75 系统消息订阅、事件扇出与站内信 API。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    InAppMessageListResponse,
    InAppMessageResponse,
    NotificationChannelType,
    NotificationEventCreate,
    NotificationEventFanoutResponse,
    NotificationRuleStatus,
    SystemMessageSubscriptionCreate,
    SystemMessageSubscriptionListResponse,
    SystemMessageSubscriptionResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.webhook import InAppMessage, SystemMessageSubscription, WebhookEndpoint
from app.services.notification_channels import CHANNEL_INBOX, parse_event_types
from app.services.notification_fanout import NotificationFanoutError, enqueue_notification_event

router = APIRouter(tags=["Notification Subscriptions"])


@router.get(
    "/notification-subscriptions/",
    response_model=SystemMessageSubscriptionListResponse,
)
async def list_notification_subscriptions(
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
    items = [
        _subscription_response(subscription, endpoint=endpoint)
        for subscription, endpoint in result.all()
    ]
    return SystemMessageSubscriptionListResponse(items=items, total=len(items))


@router.post(
    "/notification-subscriptions/",
    response_model=SystemMessageSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_subscription(
    data: SystemMessageSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMessageSubscriptionResponse:
    """创建系统消息订阅。inbox 渠道必须指定 `recipient_user_id`。"""

    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = await _get_active_endpoint(db=db, tenant_id=tenant_id, endpoint_id=data.webhook_endpoint_id)
    recipient = (data.recipient_user_id or "").strip() or None
    if endpoint.channel_type == CHANNEL_INBOX and not recipient:
        raise HTTPException(status_code=400, detail="INBOX_RECIPIENT_REQUIRED")
    subscription = SystemMessageSubscription(
        tenant_id=tenant_id,
        name=data.name,
        event_types_json=json.dumps(data.event_types),
        webhook_endpoint_id=endpoint.id,
        recipient_user_id=recipient,
        status=data.status.value,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return _subscription_response(subscription, endpoint=endpoint)


@router.post(
    "/notification-events/",
    response_model=NotificationEventFanoutResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fanout_notification_event(
    data: NotificationEventCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationEventFanoutResponse:
    """按当前租户规则与订阅扇出事件。响应不回显 payload。"""

    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    try:
        deliveries = await enqueue_notification_event(
            db=db,
            tenant_id=tenant_id,
            event_type=data.event_type,
            payload=data.payload,
            recipient_user_id=data.recipient_user_id,
        )
    except NotificationFanoutError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from None
    return NotificationEventFanoutResponse(
        event_type=data.event_type,
        created=len(deliveries),
        delivery_ids=[item.id for item in deliveries],
    )


@router.get("/in-app-messages/", response_model=InAppMessageListResponse)
async def list_in_app_messages(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageListResponse:
    """只返回当前用户在当前租户的站内信，不要求通知管理权限。"""

    tenant_id = str(user.get("tenant_id") or "default")
    recipient = str(user.get("id") or "")
    result = await db.execute(
        select(InAppMessage)
        .where(
            InAppMessage.tenant_id == tenant_id,
            InAppMessage.recipient_user_id == recipient,
        )
        .order_by(InAppMessage.id.desc())
    )
    messages = result.scalars().all()
    items = [_in_app_message_response(item) for item in messages]
    return InAppMessageListResponse(items=items, total=len(items))


def _require_notification_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _get_active_endpoint(
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
        event_types=parse_event_types(subscription.event_types_json),
        webhook_endpoint_id=subscription.webhook_endpoint_id,
        webhook_endpoint_name=endpoint.name,
        channel_type=NotificationChannelType(endpoint.channel_type or "webhook"),
        recipient_user_id=subscription.recipient_user_id,
        status=NotificationRuleStatus(subscription.status),
        created_at=_as_utc(subscription.created_at),
        updated_at=_as_utc(subscription.updated_at),
    )


def _in_app_message_response(message: InAppMessage) -> InAppMessageResponse:
    parsed: Any = json.loads(message.body_json or "{}")
    body = parsed if isinstance(parsed, dict) else {}
    return InAppMessageResponse(
        id=message.id,
        tenant_id=message.tenant_id,
        recipient_user_id=message.recipient_user_id,
        event_type=message.event_type,
        title=message.title,
        body={str(key): value for key, value in body.items()},
        read_at=_as_utc(message.read_at),
        created_at=_as_utc(message.created_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
