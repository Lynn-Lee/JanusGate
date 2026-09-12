"""系统消息订阅与事件扇出。"""
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    InAppMessageListResponse,
    InAppMessageResponse,
    NotificationDeliveryStatus,
    NotificationEventCreate,
    NotificationEventFanoutResponse,
    SystemMessageSubscriptionCreate,
    SystemMessageSubscriptionListResponse,
    SystemMessageSubscriptionResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.webhook import (
    InAppMessage,
    NotificationDelivery,
    NotificationRule,
    SystemMessageSubscription,
    WebhookEndpoint,
)
from app.services.notification_channels import event_types
from app.services.notification_redact import payload_json

events_router = APIRouter(prefix="/notification-events", tags=["Notification Events"])
subscriptions_router = APIRouter(
    prefix="/notification-subscriptions", tags=["Notification Subscriptions"]
)
inbox_router = APIRouter(prefix="/in-app-messages", tags=["In-App Messages"])


def _require_notification_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@events_router.post("/", response_model=NotificationEventFanoutResponse, status_code=status.HTTP_202_ACCEPTED)
async def fanout_notification_event(
    data: NotificationEventCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationEventFanoutResponse:
    """按当前租户通知规则与系统消息订阅扇出投递记录。payload 入库前脱敏。"""
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    now = datetime.now(UTC)
    payload = payload_json(data.payload)
    endpoint_ids: set[int] = set()

    rule_rows = await db.execute(
        select(NotificationRule, WebhookEndpoint)
        .join(
            WebhookEndpoint,
            (WebhookEndpoint.id == NotificationRule.webhook_endpoint_id)
            & (WebhookEndpoint.tenant_id == NotificationRule.tenant_id),
        )
        .where(
            NotificationRule.tenant_id == tenant_id,
            NotificationRule.status == "active",
            WebhookEndpoint.status == "active",
        )
    )
    for rule, endpoint in rule_rows.all():
        if data.event_type in event_types(rule.event_types_json) and data.event_type in event_types(
            endpoint.event_types_json
        ):
            endpoint_ids.add(endpoint.id)

    subject = (data.subject_user_id or "").strip()
    user_filter = (
        or_(
            SystemMessageSubscription.user_id == "",
            SystemMessageSubscription.user_id == subject,
        )
        if subject
        else SystemMessageSubscription.user_id == ""
    )
    sub_rows = await db.execute(
        select(SystemMessageSubscription, WebhookEndpoint)
        .join(
            WebhookEndpoint,
            (WebhookEndpoint.id == SystemMessageSubscription.webhook_endpoint_id)
            & (WebhookEndpoint.tenant_id == SystemMessageSubscription.tenant_id),
        )
        .where(
            SystemMessageSubscription.tenant_id == tenant_id,
            SystemMessageSubscription.status == "active",
            SystemMessageSubscription.event_type == data.event_type,
            WebhookEndpoint.status == "active",
            user_filter,
        )
    )
    for _subscription, endpoint in sub_rows.all():
        if data.event_type in event_types(endpoint.event_types_json):
            endpoint_ids.add(endpoint.id)

    delivery_ids: list[int] = []
    for endpoint_id in sorted(endpoint_ids):
        rule_id = await _rule_id_for_endpoint(
            db=db,
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
            event_type=data.event_type,
        )
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_rule_id=rule_id,
            webhook_endpoint_id=endpoint_id,
            event_type=data.event_type,
            payload_json=payload,
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


async def _rule_id_for_endpoint(
    *, db: AsyncSession, tenant_id: str, endpoint_id: int, event_type: str
) -> int:
    """投递表仍要求 notification_rule_id。无匹配规则时创建一条系统订阅影子规则。"""
    result = await db.execute(
        select(NotificationRule).where(
            NotificationRule.tenant_id == tenant_id,
            NotificationRule.webhook_endpoint_id == endpoint_id,
            NotificationRule.status == "active",
        )
    )
    for rule in result.scalars().all():
        if event_type in event_types(rule.event_types_json):
            return rule.id
    rule = NotificationRule(
        tenant_id=tenant_id,
        name=f"subscription:{endpoint_id}:{event_type}"[:120],
        event_types_json=f'["{event_type}"]',
        webhook_endpoint_id=endpoint_id,
        status="active",
    )
    db.add(rule)
    await db.flush()
    return rule.id


@subscriptions_router.get("/", response_model=SystemMessageSubscriptionListResponse)
async def list_subscriptions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMessageSubscriptionListResponse:
    _require_notification_permission(user, "notifications:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SystemMessageSubscription)
        .where(SystemMessageSubscription.tenant_id == tenant_id)
        .order_by(SystemMessageSubscription.id)
    )
    items = [_subscription_response(row) for row in result.scalars().all()]
    return SystemMessageSubscriptionListResponse(items=items, total=len(items))


@subscriptions_router.post(
    "/",
    response_model=SystemMessageSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    data: SystemMessageSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SystemMessageSubscriptionResponse:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = (
        await db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == data.webhook_endpoint_id,
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.status == "active",
            )
        )
    ).scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="WEBHOOK_ENDPOINT_NOT_FOUND")
    if data.event_type not in event_types(endpoint.event_types_json):
        raise HTTPException(status_code=400, detail="NOTIFICATION_EVENT_NOT_ALLOWED")
    subscription = SystemMessageSubscription(
        tenant_id=tenant_id,
        event_type=data.event_type,
        webhook_endpoint_id=endpoint.id,
        user_id=(data.user_id or "").strip(),
        status="active",
    )
    db.add(subscription)
    await db.commit()
    await db.refresh(subscription)
    return _subscription_response(subscription)


@inbox_router.get("/", response_model=InAppMessageListResponse)
async def list_in_app_messages(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageListResponse:
    """当前用户站内信。跨用户不可见。"""
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id") or "")
    result = await db.execute(
        select(InAppMessage)
        .where(InAppMessage.tenant_id == tenant_id, InAppMessage.user_id == user_id)
        .order_by(InAppMessage.id.desc())
    )
    items = [_inbox_response(row) for row in result.scalars().all()]
    return InAppMessageListResponse(items=items, total=len(items))


def _subscription_response(
    subscription: SystemMessageSubscription,
) -> SystemMessageSubscriptionResponse:
    return SystemMessageSubscriptionResponse(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        event_type=subscription.event_type,
        webhook_endpoint_id=subscription.webhook_endpoint_id,
        user_id=subscription.user_id,
        status=subscription.status,
        created_at=_as_utc(subscription.created_at),
        updated_at=_as_utc(subscription.updated_at),
    )


def _inbox_response(message: InAppMessage) -> InAppMessageResponse:
    return InAppMessageResponse(
        id=message.id,
        tenant_id=message.tenant_id,
        user_id=message.user_id,
        event_type=message.event_type,
        title=message.title,
        body=message.body,
        delivery_id=message.delivery_id,
        read_at=_as_utc(message.read_at),
        created_at=_as_utc(message.created_at),
    )
