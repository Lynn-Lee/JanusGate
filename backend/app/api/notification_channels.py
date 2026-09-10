"""#t75 系统消息订阅、事件扇出与站内信 API。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.notification_deliveries import _redact_payload
from app.api.webhook_schemas import (
    InAppMessageListResponse,
    InAppMessageResponse,
    NotificationChannelType,
    NotificationDeliveryStatus,
    NotificationEventCreate,
    NotificationEventFanoutResponse,
    SystemMessageSubscriptionCreate,
    SystemMessageSubscriptionListResponse,
    SystemMessageSubscriptionResponse,
    SystemMessageSubscriptionStatus,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.webhook import (
    InAppMessage,
    NotificationDelivery,
    SystemMessageSubscription,
    WebhookEndpoint,
)

router = APIRouter(tags=["Notification Channels"])


@router.get("/notification-subscriptions/", response_model=SystemMessageSubscriptionListResponse)
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
    items = [_subscription_response(row, endpoint) for row, endpoint in result.all()]
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
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = await _get_active_channel(db=db, tenant_id=tenant_id, endpoint_id=data.webhook_endpoint_id)
    subscription = SystemMessageSubscription(
        tenant_id=tenant_id,
        user_id=data.user_id.strip(),
        webhook_endpoint_id=endpoint.id,
        event_types_json=json.dumps(data.event_types),
        status=data.status.value,
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return _subscription_response(subscription, endpoint)


@router.delete("/notification-subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_subscription(
    subscription_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> None:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SystemMessageSubscription).where(
            SystemMessageSubscription.id == subscription_id,
            SystemMessageSubscription.tenant_id == tenant_id,
        )
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        raise HTTPException(status_code=404, detail="NOTIFICATION_SUBSCRIPTION_NOT_FOUND")
    await db.delete(subscription)
    await db.commit()


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
    """按当前租户的系统消息订阅扇出投递队列，沿用脱敏 payload 与死信契约。"""
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SystemMessageSubscription, WebhookEndpoint)
        .join(
            WebhookEndpoint,
            (WebhookEndpoint.id == SystemMessageSubscription.webhook_endpoint_id)
            & (WebhookEndpoint.tenant_id == SystemMessageSubscription.tenant_id),
        )
        .where(
            SystemMessageSubscription.tenant_id == tenant_id,
            SystemMessageSubscription.status == "active",
            WebhookEndpoint.status == "active",
        )
        .order_by(SystemMessageSubscription.id)
    )
    redacted = _redact_payload(data.payload)
    payload_json = json.dumps(redacted, sort_keys=True, default=str)
    now = datetime.now(UTC)
    delivery_ids: list[int] = []
    for subscription, endpoint in result.all():
        if data.event_type not in _event_types(subscription.event_types_json):
            continue
        if data.event_type not in _event_types(endpoint.event_types_json):
            continue
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_rule_id=None,
            webhook_endpoint_id=endpoint.id,
            recipient_user_id=subscription.user_id,
            event_type=data.event_type,
            payload_json=payload_json,
            status=NotificationDeliveryStatus.PENDING.value,
            attempts=0,
            next_attempt_at=now,
        )
        db.add(delivery)
        await db.flush()
        delivery_ids.append(delivery.id)
    await db.commit()
    return NotificationEventFanoutResponse(
        event_type=data.event_type,
        enqueued=len(delivery_ids),
        delivery_ids=delivery_ids,
    )


@router.get("/in-app-messages/", response_model=InAppMessageListResponse)
async def list_in_app_messages(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageListResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or "")
    result = await db.execute(
        select(InAppMessage)
        .where(InAppMessage.tenant_id == tenant_id, InAppMessage.user_id == user_id)
        .order_by(InAppMessage.id)
    )
    messages = result.scalars().all()
    items = [_in_app_message_response(item) for item in messages]
    return InAppMessageListResponse(items=items, total=len(items))


@router.post("/in-app-messages/{message_id}/read", response_model=InAppMessageResponse)
async def mark_in_app_message_read(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or "")
    result = await db.execute(
        select(InAppMessage).where(
            InAppMessage.id == message_id,
            InAppMessage.tenant_id == tenant_id,
            InAppMessage.user_id == user_id,
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="IN_APP_MESSAGE_NOT_FOUND")
    message.read_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(message)
    return _in_app_message_response(message)


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
    subscription: SystemMessageSubscription, endpoint: WebhookEndpoint
) -> SystemMessageSubscriptionResponse:
    return SystemMessageSubscriptionResponse(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        user_id=subscription.user_id,
        webhook_endpoint_id=subscription.webhook_endpoint_id,
        webhook_endpoint_name=endpoint.name,
        channel_type=NotificationChannelType(endpoint.channel_type or "webhook"),
        event_types=_event_types(subscription.event_types_json),
        status=SystemMessageSubscriptionStatus(subscription.status),
        created_at=_as_utc(subscription.created_at),
        updated_at=_as_utc(subscription.updated_at),
    )


def _in_app_message_response(message: InAppMessage) -> InAppMessageResponse:
    parsed = json.loads(message.body_json)
    payload = parsed if isinstance(parsed, dict) else {}
    return InAppMessageResponse(
        id=message.id,
        tenant_id=message.tenant_id,
        user_id=message.user_id,
        delivery_id=message.delivery_id,
        event_type=message.event_type,
        title=message.title,
        payload=payload,
        read_at=_as_utc(message.read_at),
        created_at=_as_utc(message.created_at),
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
