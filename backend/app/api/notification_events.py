"""通知事件扇出：匹配规则与系统消息订阅后写入可靠投递队列。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    NotificationDeliveryStatus,
    NotificationEventCreate,
    NotificationEventFanoutResponse,
)
from app.core.database import get_db
from app.core.deps import current_user
from app.models.webhook import (
    NotificationDelivery,
    NotificationRule,
    SystemMessageSubscription,
    WebhookEndpoint,
)
from app.services.notification_payload import event_types, redact_payload

router = APIRouter(prefix="/notification-events", tags=["Notification Events"])


@router.post("/", response_model=NotificationEventFanoutResponse, status_code=status.HTTP_202_ACCEPTED)
async def publish_notification_event(
    data: NotificationEventCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationEventFanoutResponse:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    payload_json = json.dumps(redact_payload(data.payload), sort_keys=True, default=str)
    now = datetime.now(UTC)
    delivery_ids: list[int] = []

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
        .order_by(NotificationRule.id)
    )
    for rule, endpoint in rule_rows.all():
        if data.event_type not in event_types(rule.event_types_json):
            continue
        if data.event_type not in event_types(endpoint.event_types_json):
            continue
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_rule_id=rule.id,
            subscription_id=None,
            webhook_endpoint_id=endpoint.id,
            recipient_user_id=None,
            event_type=data.event_type,
            payload_json=payload_json,
            status=NotificationDeliveryStatus.PENDING.value,
            attempts=0,
            next_attempt_at=now,
        )
        db.add(delivery)
        await db.flush()
        delivery_ids.append(delivery.id)

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
            WebhookEndpoint.status == "active",
        )
        .order_by(SystemMessageSubscription.id)
    )
    for subscription, endpoint in sub_rows.all():
        if data.event_type not in event_types(subscription.event_types_json):
            continue
        if data.event_type not in event_types(endpoint.event_types_json):
            continue
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_rule_id=None,
            subscription_id=subscription.id,
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


def _require_notification_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
