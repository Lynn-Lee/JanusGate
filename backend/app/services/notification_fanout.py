"""按规则与系统消息订阅把事件扇出到投递队列。"""
from __future__ import annotations

import json
from dataclasses import dataclass
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
    CHANNEL_WEBHOOK,
    parse_event_types,
    redact_notification_payload,
)


@dataclass(frozen=True)
class NotificationFanoutResult:
    """扇出结果只暴露计数，不回显 payload 或凭据。"""

    event_type: str
    queued: int
    inbox_queued: int


async def fanout_notification_event(
    *,
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> NotificationFanoutResult:
    """匹配当前租户 active 规则与订阅，写入已脱敏投递记录。

    同一渠道 + 接收人只入队一次，避免规则与订阅重复投递。站内信必须带
    ``recipient_user_id``，否则跳过该订阅。
    """
    effective_now = now or datetime.now(UTC)
    redacted = redact_notification_payload(payload)
    if not isinstance(redacted, dict):
        redacted = {}
    payload_json = json.dumps(redacted, sort_keys=True, default=str)

    targets = await _collect_targets(
        db=db, tenant_id=tenant_id, event_type=event_type
    )
    queued = 0
    inbox_queued = 0
    for target in targets:
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_rule_id=target.rule_id,
            subscription_id=target.subscription_id,
            webhook_endpoint_id=target.endpoint_id,
            recipient_user_id=target.recipient_user_id,
            event_type=event_type,
            payload_json=payload_json,
            status="pending",
            attempts=0,
            next_attempt_at=effective_now,
        )
        db.add(delivery)
        queued += 1
        if target.channel_type == CHANNEL_INBOX:
            inbox_queued += 1
    await db.commit()
    return NotificationFanoutResult(
        event_type=event_type, queued=queued, inbox_queued=inbox_queued
    )


@dataclass(frozen=True)
class _FanoutTarget:
    endpoint_id: int
    channel_type: str
    rule_id: int | None
    subscription_id: int | None
    recipient_user_id: str | None


async def _collect_targets(
    *, db: AsyncSession, tenant_id: str, event_type: str
) -> list[_FanoutTarget]:
    seen: set[tuple[int, str]] = set()
    targets: list[_FanoutTarget] = []

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
        if endpoint.channel_type == CHANNEL_INBOX:
            continue
        if event_type not in parse_event_types(rule.event_types_json):
            continue
        if event_type not in parse_event_types(endpoint.event_types_json):
            continue
        key = (endpoint.id, "")
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            _FanoutTarget(
                endpoint_id=endpoint.id,
                channel_type=endpoint.channel_type or CHANNEL_WEBHOOK,
                rule_id=rule.id,
                subscription_id=None,
                recipient_user_id=None,
            )
        )

    subscription_rows = await db.execute(
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
    for subscription, endpoint in subscription_rows.all():
        if event_type not in parse_event_types(subscription.event_types_json):
            continue
        if event_type not in parse_event_types(endpoint.event_types_json):
            continue
        recipient = (subscription.recipient_user_id or "").strip() or None
        if endpoint.channel_type == CHANNEL_INBOX and not recipient:
            continue
        key = (endpoint.id, recipient or "")
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            _FanoutTarget(
                endpoint_id=endpoint.id,
                channel_type=endpoint.channel_type or CHANNEL_WEBHOOK,
                rule_id=None,
                subscription_id=subscription.id,
                recipient_user_id=recipient,
            )
        )
    return targets
