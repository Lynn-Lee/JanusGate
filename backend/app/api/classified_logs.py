"""#t78 操作日志 / 改密日志入库与只读列表。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import AuditCategory, AuditEventCreate, AuditSeverity
from app.api.audits.service import audit_service
from app.core.database import get_read_db
from app.core.deps import current_user
from app.models.classified_log import OperateLog, PasswordChangeLog

router = APIRouter(tags=["分类审计"])


class OperateLogResponse(BaseModel):
    id: int
    tenant_id: str
    actor_id: str
    actor_username: str
    resource_type: str
    resource_id: str
    action: str
    summary: str
    audit_event_id: str
    occurred_at: datetime | None


class OperateLogListResponse(BaseModel):
    items: list[OperateLogResponse]
    total: int


class PasswordChangeLogResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str
    username: str
    method: str
    audit_event_id: str
    occurred_at: datetime | None


class PasswordChangeLogListResponse(BaseModel):
    items: list[PasswordChangeLogResponse]
    total: int


def _require_classified_audit_read(user: dict[str, Any]) -> None:
    """分类日志对审计员可见；admin 作通配符。"""

    permissions = user.get("permissions", [])
    if "admin" in permissions or "audit:read" in permissions:
        return
    raise HTTPException(status_code=403, detail="缺少权限: audit:read")


def _actor_tenant(user: dict[str, Any]) -> str:
    return str(user.get("tenant_id") or "default")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def persist_operate_log(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    resource_type: str,
    resource_id: str,
    action: str,
    summary: str,
) -> OperateLog:
    """先写 hash chain，再落操作分类日志。summary 不得含凭据。"""

    actor = dict(user)
    actor.setdefault("tenant_id", "default")
    event = await audit_service.create_event(
        AuditEventCreate(
            event_type="admin.operate",
            category=AuditCategory.audit,
            action=f"operate.{action}",
            resource_type=resource_type,
            resource_id=str(resource_id),
            severity=AuditSeverity.low,
            message="Operate log recorded",
            metadata={
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "action": action,
                "summary": summary,
            },
        ),
        actor,
    )
    row = OperateLog(
        tenant_id=_actor_tenant(user),
        actor_id=str(user.get("id") or ""),
        actor_username=str(user.get("username") or ""),
        resource_type=resource_type,
        resource_id=str(resource_id),
        action=action,
        summary=summary,
        audit_event_id=event.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def persist_password_change_log(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    method: str = "self",
) -> PasswordChangeLog:
    """成功改密后入库；metadata 只含用户标识与方式，不含密码。"""

    actor = dict(user)
    actor.setdefault("tenant_id", "default")
    user_id = str(user.get("id") or "")
    username = str(user.get("username") or "")
    event = await audit_service.create_event(
        AuditEventCreate(
            event_type="auth.password_change",
            category=AuditCategory.auth,
            action="password.change",
            resource_type="user",
            resource_id=user_id,
            severity=AuditSeverity.medium,
            message="Password changed",
            metadata={"method": method, "username": username},
        ),
        actor,
    )
    row = PasswordChangeLog(
        tenant_id=_actor_tenant(user),
        user_id=user_id,
        username=username,
        method=method,
        audit_event_id=event.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/operate-logs/", response_model=OperateLogListResponse)
async def list_operate_logs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> OperateLogListResponse:
    """当前租户操作日志；不返回凭据或请求体。"""

    _require_classified_audit_read(user)
    tenant_id = _actor_tenant(user)
    result = await db.execute(
        select(OperateLog)
        .where(OperateLog.tenant_id == tenant_id)
        .order_by(OperateLog.occurred_at.desc(), OperateLog.id.desc())
    )
    logs = result.scalars().all()
    items = [
        OperateLogResponse(
            id=log.id,
            tenant_id=log.tenant_id,
            actor_id=log.actor_id,
            actor_username=log.actor_username,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            action=log.action,
            summary=log.summary,
            audit_event_id=log.audit_event_id,
            occurred_at=_as_utc(log.occurred_at),
        )
        for log in logs
    ]
    return OperateLogListResponse(items=items, total=len(items))


@router.get("/password-change-logs/", response_model=PasswordChangeLogListResponse)
async def list_password_change_logs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> PasswordChangeLogListResponse:
    """当前租户改密日志；不含密码哈希或明文。"""

    _require_classified_audit_read(user)
    tenant_id = _actor_tenant(user)
    result = await db.execute(
        select(PasswordChangeLog)
        .where(PasswordChangeLog.tenant_id == tenant_id)
        .order_by(PasswordChangeLog.occurred_at.desc(), PasswordChangeLog.id.desc())
    )
    logs = result.scalars().all()
    items = [
        PasswordChangeLogResponse(
            id=log.id,
            tenant_id=log.tenant_id,
            user_id=log.user_id,
            username=log.username,
            method=log.method,
            audit_event_id=log.audit_event_id,
            occurred_at=_as_utc(log.occurred_at),
        )
        for log in logs
    ]
    return PasswordChangeLogListResponse(items=items, total=len(items))
