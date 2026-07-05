"""Phase 4 notification rule management API routes."""
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import (
    NotificationRuleCreate,
    NotificationRuleListResponse,
    NotificationRuleResponse,
    NotificationRuleStatus,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.webhook import NotificationRule, WebhookEndpoint

router = APIRouter(prefix="/notification-rules", tags=["Notification Rules"])


@router.get("/", response_model=NotificationRuleListResponse)
async def list_notification_rules(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationRuleListResponse:
    _require_notification_permission(user, "notifications:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(NotificationRule, WebhookEndpoint.name)
        .join(
            WebhookEndpoint,
            (WebhookEndpoint.id == NotificationRule.webhook_endpoint_id)
            & (WebhookEndpoint.tenant_id == NotificationRule.tenant_id),
        )
        .where(NotificationRule.tenant_id == tenant_id)
        .order_by(NotificationRule.id)
    )
    rows = result.all()
    items = [
        _notification_rule_response(rule, webhook_endpoint_name=endpoint_name)
        for rule, endpoint_name in rows
    ]
    return NotificationRuleListResponse(items=items, total=len(items))


@router.post("/", response_model=NotificationRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_notification_rule(
    data: NotificationRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NotificationRuleResponse:
    _require_notification_permission(user, "notifications:write")
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = await _get_active_webhook_endpoint(
        db=db, tenant_id=tenant_id, endpoint_id=data.webhook_endpoint_id
    )
    rule = NotificationRule(
        tenant_id=tenant_id,
        name=data.name,
        event_types_json=json.dumps(data.event_types),
        webhook_endpoint_id=endpoint.id,
        status=data.status.value,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _notification_rule_response(rule, webhook_endpoint_name=endpoint.name)


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


def _notification_rule_response(
    rule: NotificationRule, *, webhook_endpoint_name: str
) -> NotificationRuleResponse:
    return NotificationRuleResponse(
        id=rule.id,
        tenant_id=rule.tenant_id,
        name=rule.name,
        event_types=_event_types(rule.event_types_json),
        webhook_endpoint_id=rule.webhook_endpoint_id,
        webhook_endpoint_name=webhook_endpoint_name,
        status=NotificationRuleStatus(rule.status),
        created_at=_as_utc(rule.created_at),
        updated_at=_as_utc(rule.updated_at),
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
