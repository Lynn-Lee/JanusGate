"""#t75 系统消息扇出：匹配通知规则与订阅，写入 #t47 投递队列。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import (
    NotificationDelivery,
    NotificationRule,
    SystemMessageSubscription,
    WebhookEndpoint,
)
from app.services.notification_channels import (
    CHANNEL_INBOX,
    parse_event_types,
    redact_notification_payload,
)


class NotificationFanoutError(ValueError):
    """扇出失败。`code` 为稳定业务错误码，不得包含 payload 或凭据。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def enqueue_notification_event(
    *,
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    recipient_user_id: str | None = None,
) -> list[NotificationDelivery]:
    """按当前租户 active 规则与订阅扇出。payload 入库前脱敏。

    inbox 订阅缺少接收人时 fail-closed 为 `INBOX_RECIPIENT_REQUIRED`，不部分写入。
    """

    redacted = redact_notification_payload(payload)
    payload_json = json.dumps(redacted, sort_keys=True, default=str)
    now = datetime.now(UTC)
    targets = await _collect_targets(
        db=db,
        tenant_id=tenant_id,
        event_type=event_type,
        recipient_user_id=recipient_user_id,
    )
    deliveries: list[NotificationDelivery] = []
    for endpoint, rule_id, recipient in targets:
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_rule_id=rule_id,
            webhook_endpoint_id=endpoint.id,
            recipient_user_id=recipient,
            event_type=event_type,
            payload_json=payload_json,
            status="pending",
            attempts=0,
            next_attempt_at=now,
        )
        db.add(delivery)
        deliveries.append(delivery)
    await db.commit()
    for delivery in deliveries:
        await db.refresh(delivery)
    return deliveries


async def _collect_targets(
    *,
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    recipient_user_id: str | None,
) -> list[tuple[WebhookEndpoint, int | None, str | None]]:
    targets: list[tuple[WebhookEndpoint, int | None, str | None]] = []
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
        if event_type not in parse_event_types(rule.event_types_json):
            continue
        if event_type not in parse_event_types(endpoint.event_types_json):
            continue
        if endpoint.channel_type == CHANNEL_INBOX:
            continue
        targets.append((endpoint, rule.id, None))

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
    )
    for subscription, endpoint in sub_rows.all():
        if event_type not in parse_event_types(subscription.event_types_json):
            continue
        if event_type not in parse_event_types(endpoint.event_types_json):
            continue
        recipient = (subscription.recipient_user_id or recipient_user_id or "").strip() or None
        if endpoint.channel_type == CHANNEL_INBOX and not recipient:
            raise NotificationFanoutError("INBOX_RECIPIENT_REQUIRED")
        targets.append((endpoint, None, recipient))
    return targets
