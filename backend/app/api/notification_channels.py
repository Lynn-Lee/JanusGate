"""#t75 notification channels, system-msg subscriptions, and inbox APIs."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.notification_channel_schemas import (
    InboxMessageListResponse,
    InboxMessageResponse,
    NotificationChannelCreate,
    NotificationChannelListResponse,
    NotificationChannelResponse,
    NotificationChannelStatus,
    NotificationChannelType,
    SystemMsgSubscriptionResponse,
    SystemMsgSubscriptionUpdate,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.notification_channel import (
    CHANNEL_INBOX,
    CHANNEL_WEBHOOK,
    HTTPS_IM_CHANNEL_TYPES,
    STUB_CHANNEL_TYPES,
    InboxMessage,
    NotificationChannel,
    SystemMsgSubscription,
)

channels_router = APIRouter(prefix="/notification-channels", tags=["Notification Channels"])
subscriptions_router = APIRouter(
    prefix="/system-msg-subscriptions", tags=["System Message Subscriptions"]
)
inbox_router = APIRouter(prefix="/inbox-messages", tags=["Inbox Messages"])


@channels_router.get("/", response_model=NotificationChannelListResponse)
async def list_notification_channels(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationChannelListResponse:
    _require_notification_permission(user, "notifications:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(NotificationChannel)
        .where(NotificationChannel.tenant_id == tenant_id)
        .order_by(NotificationChannel.id)
    )
    items = [_channel_response(channel) for channel in result.scalars().all()]
    return NotificationChannelListResponse(items=items, total=len(items))


@channels_router.post(
    "/",
    response_model=NotificationChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_channel(
    data: NotificationChannelCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationChannelResponse:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    channel_type = data.channel_type.value
    if channel_type in STUB_CHANNEL_TYPES:
        raise HTTPException(status_code=400, detail="CHANNEL_TYPE_NOT_IMPLEMENTED")
    config = _validated_channel_config(channel_type=channel_type, config=data.config)
    channel = NotificationChannel(
        tenant_id=tenant_id,
        name=data.name,
        channel_type=channel_type,
        event_types_json=json.dumps(data.event_types),
        config_json=json.dumps(config, sort_keys=True),
        status=data.status.value,
    )
    db.add(channel)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="NOTIFICATION_CHANNEL_CREATE_FAILED") from exc
    await db.refresh(channel)
    return _channel_response(channel)


@subscriptions_router.get("/me", response_model=SystemMsgSubscriptionResponse)
async def get_my_system_msg_subscription(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMsgSubscriptionResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or user.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="未认证")
    subscription = await _get_subscription(db, tenant_id=tenant_id, user_id=user_id)
    if subscription is None:
        return SystemMsgSubscriptionResponse(
            id=0,
            tenant_id=tenant_id,
            user_id=user_id,
            event_types=[],
            channel_types=[CHANNEL_INBOX],
            status=NotificationChannelStatus.DISABLED,
            created_at=None,
            updated_at=None,
        )
    return _subscription_response(subscription)


@subscriptions_router.put("/me", response_model=SystemMsgSubscriptionResponse)
async def upsert_my_system_msg_subscription(
    data: SystemMsgSubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMsgSubscriptionResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or user.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="未认证")
    subscription = await _get_subscription(db, tenant_id=tenant_id, user_id=user_id)
    if subscription is None:
        subscription = SystemMsgSubscription(
            tenant_id=tenant_id,
            user_id=user_id,
            event_types_json=json.dumps(data.event_types),
            channel_types_json=json.dumps(data.channel_types),
            status=data.status.value,
        )
        db.add(subscription)
    else:
        subscription.event_types_json = json.dumps(data.event_types)
        subscription.channel_types_json = json.dumps(data.channel_types)
        subscription.status = data.status.value
    await db.commit()
    await db.refresh(subscription)
    return _subscription_response(subscription)


@inbox_router.get("/", response_model=InboxMessageListResponse)
async def list_my_inbox_messages(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> InboxMessageListResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or user.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="未认证")
    result = await db.execute(
        select(InboxMessage)
        .where(
            InboxMessage.tenant_id == tenant_id,
            InboxMessage.user_id == user_id,
        )
        .order_by(InboxMessage.id.desc())
    )
    items = [_inbox_response(message) for message in result.scalars().all()]
    return InboxMessageListResponse(items=items, total=len(items))


@inbox_router.post("/{message_id}/read", response_model=InboxMessageResponse)
async def mark_inbox_message_read(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> InboxMessageResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or user.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="未认证")
    result = await db.execute(
        select(InboxMessage).where(
            InboxMessage.id == message_id,
            InboxMessage.tenant_id == tenant_id,
            InboxMessage.user_id == user_id,
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="INBOX_MESSAGE_NOT_FOUND")
    if message.read_at is None:
        message.read_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(message)
    return _inbox_response(message)


def _require_notification_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _validated_channel_config(*, channel_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if channel_type == CHANNEL_INBOX:
        user_id = str(config.get("user_id") or "").strip()
        return {"user_id": user_id} if user_id else {}
    if channel_type in HTTPS_IM_CHANNEL_TYPES | {CHANNEL_WEBHOOK}:
        url = str(config.get("webhook_url") or "").strip()
        if not url.lower().startswith("https://"):
            raise HTTPException(status_code=400, detail="INVALID_CHANNEL_WEBHOOK_URL")
        return {"webhook_url": url}
    raise HTTPException(status_code=400, detail="CHANNEL_TYPE_NOT_IMPLEMENTED")


async def _get_subscription(
    db: AsyncSession, *, tenant_id: str, user_id: str
) -> SystemMsgSubscription | None:
    result = await db.execute(
        select(SystemMsgSubscription).where(
            SystemMsgSubscription.tenant_id == tenant_id,
            SystemMsgSubscription.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _channel_response(channel: NotificationChannel) -> NotificationChannelResponse:
    return NotificationChannelResponse(
        id=channel.id,
        tenant_id=channel.tenant_id,
        name=channel.name,
        channel_type=NotificationChannelType(channel.channel_type),
        event_types=_string_list(channel.event_types_json),
        config=_public_config(channel),
        status=NotificationChannelStatus(channel.status),
        created_at=_as_utc(channel.created_at),
        updated_at=_as_utc(channel.updated_at),
    )


def _public_config(channel: NotificationChannel) -> dict[str, Any]:
    try:
        parsed = json.loads(channel.config_json or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Never echo secrets; current configs only store webhook_url / user_id.
    allowed = {"webhook_url", "user_id"}
    return {str(key): value for key, value in parsed.items() if str(key) in allowed}


def _subscription_response(
    subscription: SystemMsgSubscription,
) -> SystemMsgSubscriptionResponse:
    return SystemMsgSubscriptionResponse(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        user_id=subscription.user_id,
        event_types=_string_list(subscription.event_types_json),
        channel_types=_string_list(subscription.channel_types_json),
        status=NotificationChannelStatus(subscription.status),
        created_at=_as_utc(subscription.created_at),
        updated_at=_as_utc(subscription.updated_at),
    )


def _inbox_response(message: InboxMessage) -> InboxMessageResponse:
    try:
        body = json.loads(message.body_json or "{}")
    except json.JSONDecodeError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return InboxMessageResponse(
        id=message.id,
        tenant_id=message.tenant_id,
        user_id=message.user_id,
        event_type=message.event_type,
        title=message.title,
        body=body,
        read_at=_as_utc(message.read_at),
        created_at=_as_utc(message.created_at),
    )


def _string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
