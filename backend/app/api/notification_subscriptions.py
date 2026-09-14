"""#t75 系统消息订阅、站内信与事件扇出 API。"""
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
from app.services.notification_fanout import fanout_notification_event

subscriptions_router = APIRouter(
    prefix="/system-message-subscriptions", tags=["System Message Subscriptions"]
)
events_router = APIRouter(prefix="/notification-events", tags=["Notification Events"])
inbox_router = APIRouter(prefix="/in-app-messages", tags=["In-App Messages"])


@subscriptions_router.get("/", response_model=SystemMessageSubscriptionListResponse)
async def list_system_message_subscriptions(
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


@subscriptions_router.post(
    "/",
    response_model=SystemMessageSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_system_message_subscription(
    data: SystemMessageSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMessageSubscriptionResponse:
    """创建系统消息订阅。站内信渠道必须指定接收人，否则 fail-closed。"""
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = await _get_active_webhook_endpoint(
        db=db, tenant_id=tenant_id, endpoint_id=data.webhook_endpoint_id
    )
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


@events_router.post("/", response_model=NotificationEventFanoutResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_notification_event(
    data: NotificationEventCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationEventFanoutResponse:
    """按当前租户规则与订阅扇出事件。响应只返回计数，不回显 payload。"""
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await fanout_notification_event(
        db=db,
        tenant_id=tenant_id,
        event_type=data.event_type,
        payload=data.payload,
    )
    return NotificationEventFanoutResponse(
        event_type=result.event_type,
        queued=result.queued,
        inbox_queued=result.inbox_queued,
    )


@inbox_router.get("/", response_model=InAppMessageListResponse)
async def list_in_app_messages(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageListResponse:
    """只返回当前用户在当前租户的站内信。"""
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or "")
    result = await db.execute(
        select(InAppMessage)
        .where(InAppMessage.tenant_id == tenant_id, InAppMessage.user_id == user_id)
        .order_by(InAppMessage.id.desc())
    )
    messages = result.scalars().all()
    items = [_in_app_message_response(message) for message in messages]
    return InAppMessageListResponse(items=items, total=len(items))


@inbox_router.post("/{message_id}/read", response_model=InAppMessageResponse)
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


async def _get_active_webhook_endpoint(
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
        event_type=message.event_type,
        title=message.title,
        body=body,
        read_at=_as_utc(message.read_at),
        created_at=_as_utc(message.created_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
