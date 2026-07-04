"""Phase 4 notification delivery queue API routes."""
import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    NotificationDeliveryCreate,
    NotificationDeliveryListResponse,
    NotificationDeliveryResponse,
    NotificationDeliveryStatus,
)
from app.core.database import get_db
from app.core.deps import current_user
from app.models.webhook import NotificationDelivery, NotificationRule, WebhookEndpoint

router = APIRouter(tags=["Notification Deliveries"])

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\\b(token|password|passwd|secret|credential)\\s*=\\s*[^\\s,;]+"
)


@router.get(
    "/notification-deliveries/",
    response_model=NotificationDeliveryListResponse,
)
async def list_notification_deliveries(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationDeliveryListResponse:
    _require_notification_permission(user, "notifications:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(NotificationDelivery)
        .where(NotificationDelivery.tenant_id == tenant_id)
        .order_by(NotificationDelivery.id)
    )
    deliveries = result.scalars().all()
    items = [_notification_delivery_response(delivery) for delivery in deliveries]
    return NotificationDeliveryListResponse(items=items, total=len(items))


@router.post(
    "/notification-rules/{rule_id}/deliveries",
    response_model=NotificationDeliveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_notification_delivery(
    rule_id: int,
    data: NotificationDeliveryCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationDeliveryResponse:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    rule, endpoint = await _get_active_rule_and_endpoint(db=db, tenant_id=tenant_id, rule_id=rule_id)
    if not _event_type_allowed(data.event_type, rule=rule, endpoint=endpoint):
        raise HTTPException(status_code=400, detail="NOTIFICATION_EVENT_NOT_ALLOWED")

    now = datetime.now(UTC)
    delivery = NotificationDelivery(
        tenant_id=tenant_id,
        notification_rule_id=rule.id,
        webhook_endpoint_id=endpoint.id,
        event_type=data.event_type,
        payload_json=json.dumps(_redact_payload(data.payload), sort_keys=True, default=str),
        status=NotificationDeliveryStatus.PENDING.value,
        attempts=0,
        next_attempt_at=now,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return _notification_delivery_response(delivery)


def _require_notification_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _get_active_rule_and_endpoint(
    *, db: AsyncSession, tenant_id: str, rule_id: int
) -> tuple[NotificationRule, WebhookEndpoint]:
    result = await db.execute(
        select(NotificationRule, WebhookEndpoint)
        .join(
            WebhookEndpoint,
            (WebhookEndpoint.id == NotificationRule.webhook_endpoint_id)
            & (WebhookEndpoint.tenant_id == NotificationRule.tenant_id),
        )
        .where(
            NotificationRule.id == rule_id,
            NotificationRule.tenant_id == tenant_id,
            NotificationRule.status == "active",
            WebhookEndpoint.status == "active",
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="NOTIFICATION_RULE_NOT_FOUND")
    rule, endpoint = row
    return rule, endpoint


def _event_type_allowed(
    event_type: str, *, rule: NotificationRule, endpoint: WebhookEndpoint
) -> bool:
    return event_type in _event_types(rule.event_types_json) and event_type in _event_types(
        endpoint.event_types_json
    )


def _notification_delivery_response(
    delivery: NotificationDelivery,
) -> NotificationDeliveryResponse:
    return NotificationDeliveryResponse(
        id=delivery.id,
        tenant_id=delivery.tenant_id,
        notification_rule_id=delivery.notification_rule_id,
        webhook_endpoint_id=delivery.webhook_endpoint_id,
        event_type=delivery.event_type,
        status=NotificationDeliveryStatus(delivery.status),
        attempts=delivery.attempts,
        next_attempt_at=_as_utc(delivery.next_attempt_at) or delivery.next_attempt_at,
        last_error=delivery.last_error,
        created_at=_as_utc(delivery.created_at),
        updated_at=_as_utc(delivery.updated_at),
    )


def _event_types(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_ASSIGNMENT.sub(r"\\1=[REDACTED]", value)
    return value


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
