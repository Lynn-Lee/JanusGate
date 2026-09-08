"""System message subscription fan-out into the inbox (#t75)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_channel import CHANNEL_INBOX, InboxMessage, SystemMsgSubscription
from app.services.notification_redaction import redact_payload


async def fanout_inbox_subscriptions(
    db: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    title: str | None = None,
) -> int:
    """Write InboxMessage rows for active subscriptions that include inbox + event_type.

    Payload is redacted before persistence. Returns number of messages created.
    """

    redacted = redact_payload(payload)
    result = await db.execute(
        select(SystemMsgSubscription).where(
            SystemMsgSubscription.tenant_id == tenant_id,
            SystemMsgSubscription.status == "active",
        )
    )
    created = 0
    message_title = (title or event_type)[:255]
    body_json = json.dumps(redacted, sort_keys=True, default=str)
    for subscription in result.scalars().all():
        event_types = _string_list(subscription.event_types_json)
        channel_types = _string_list(subscription.channel_types_json)
        if event_type not in event_types:
            continue
        if CHANNEL_INBOX not in channel_types:
            continue
        db.add(
            InboxMessage(
                tenant_id=tenant_id,
                user_id=subscription.user_id,
                event_type=event_type,
                title=message_title,
                body_json=body_json,
            )
        )
        created += 1
    return created


def _string_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]
