"""#t78 分类审计入库与只读列表。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import AuditCategory, AuditEventCreate, AuditSeverity
from app.api.audits.service import audit_service
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.classified_log import (
    ActivityLog,
    OnlineUserSession,
    OperateLog,
    PasswordChangeLog,
)

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


class ActivityLogResponse(BaseModel):
    id: int
    tenant_id: str
    actor_id: str
    actor_username: str
    resource_type: str
    resource_id: str
    action: str
    detail: str
    audit_event_id: str
    occurred_at: datetime | None


class ActivityLogListResponse(BaseModel):
    items: list[ActivityLogResponse]
    total: int


class OnlineUserSessionResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str
    username: str
    client_ip: str
    status: str
    audit_event_id: str
    ended_audit_event_id: str
    occurred_at: datetime | None
    ended_at: datetime | None


class OnlineUserSessionListResponse(BaseModel):
    items: list[OnlineUserSessionResponse]
    total: int


def _require_classified_audit_read(user: dict[str, Any]) -> None:
    """分类日志对审计员可见；admin 作通配符。"""

    permissions = user.get("permissions", [])
    if "admin" in permissions or "audit:read" in permissions:
        return
    raise HTTPException(status_code=403, detail="缺少权限: audit:read")


def _require_online_session_kick(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions:
        return
    raise HTTPException(status_code=403, detail="缺少权限: admin")


def _actor_tenant(user: dict[str, Any]) -> str:
    return str(user.get("tenant_id") or "default")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _online_session_response(row: OnlineUserSession) -> OnlineUserSessionResponse:
    return OnlineUserSessionResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        username=row.username,
        client_ip=row.client_ip,
        status=row.status,
        audit_event_id=row.audit_event_id,
        ended_audit_event_id=row.ended_audit_event_id,
        occurred_at=_as_utc(row.occurred_at),
        ended_at=_as_utc(row.ended_at),
    )


async def _add_activity_log(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str,
    actor_username: str,
    resource_type: str,
    resource_id: str,
    action: str,
    detail: str,
    audit_event_id: str,
) -> ActivityLog:
    """复用已有 hash chain 事件 id，不再追加第二条审计事件。"""

    row = ActivityLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_username=actor_username,
        resource_type=resource_type,
        resource_id=str(resource_id),
        action=action,
        detail=detail,
        audit_event_id=audit_event_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def persist_operate_log(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    resource_type: str,
    resource_id: str,
    action: str,
    summary: str,
) -> OperateLog:
    """先写 hash chain，再落操作分类日志，并同步活动时间线。summary 不得含凭据。"""

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
    await _add_activity_log(
        db,
        tenant_id=row.tenant_id,
        actor_id=row.actor_id,
        actor_username=row.actor_username,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        action=row.action,
        detail=row.summary,
        audit_event_id=event.id,
    )
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
    await _add_activity_log(
        db,
        tenant_id=row.tenant_id,
        actor_id=user_id,
        actor_username=username,
        resource_type="user",
        resource_id=user_id,
        action="password_change",
        detail=f"password changed via {method}",
        audit_event_id=event.id,
    )
    return row


async def persist_login_session(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    client_ip: str = "",
) -> OnlineUserSession:
    """交互式登录成功后写入在线会话与活动时间线。不保存 token。"""

    actor = dict(user)
    actor.setdefault("tenant_id", "default")
    user_id = str(user.get("id") or "")
    username = str(user.get("username") or "")
    safe_ip = (client_ip or "")[:64]
    event = await audit_service.create_event(
        AuditEventCreate(
            event_type="auth.login",
            category=AuditCategory.auth,
            action="session.login",
            resource_type="user",
            resource_id=user_id,
            severity=AuditSeverity.low,
            message="Interactive login",
            metadata={"username": username, "client_ip": safe_ip},
        ),
        actor,
    )
    row = OnlineUserSession(
        tenant_id=_actor_tenant(user),
        user_id=user_id,
        username=username,
        client_ip=safe_ip,
        status="active",
        audit_event_id=event.id,
        ended_audit_event_id="",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _add_activity_log(
        db,
        tenant_id=row.tenant_id,
        actor_id=user_id,
        actor_username=username,
        resource_type="user",
        resource_id=user_id,
        action="login",
        detail=f"interactive login from {safe_ip}" if safe_ip else "interactive login",
        audit_event_id=event.id,
    )
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


@router.get("/activity-logs/", response_model=ActivityLogListResponse)
async def list_activity_logs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ActivityLogListResponse:
    """当前租户活动时间线；不含凭据正文。"""

    _require_classified_audit_read(user)
    tenant_id = _actor_tenant(user)
    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.tenant_id == tenant_id)
        .order_by(ActivityLog.occurred_at.desc(), ActivityLog.id.desc())
    )
    logs = result.scalars().all()
    items = [
        ActivityLogResponse(
            id=log.id,
            tenant_id=log.tenant_id,
            actor_id=log.actor_id,
            actor_username=log.actor_username,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            action=log.action,
            detail=log.detail,
            audit_event_id=log.audit_event_id,
            occurred_at=_as_utc(log.occurred_at),
        )
        for log in logs
    ]
    return ActivityLogListResponse(items=items, total=len(items))


@router.get("/online-sessions/", response_model=OnlineUserSessionListResponse)
async def list_online_sessions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> OnlineUserSessionListResponse:
    """当前租户在线会话；不含 access/refresh token。"""

    _require_classified_audit_read(user)
    tenant_id = _actor_tenant(user)
    result = await db.execute(
        select(OnlineUserSession)
        .where(OnlineUserSession.tenant_id == tenant_id)
        .order_by(OnlineUserSession.occurred_at.desc(), OnlineUserSession.id.desc())
    )
    rows = result.scalars().all()
    items = [_online_session_response(row) for row in rows]
    return OnlineUserSessionListResponse(items=items, total=len(items))


@router.post("/online-sessions/{session_id}/end", response_model=OnlineUserSessionResponse)
async def end_online_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> OnlineUserSessionResponse:
    """结束当前租户在线会话。跨租户返回 404，不暴露对端是否存在。"""

    _require_online_session_kick(user)
    tenant_id = _actor_tenant(user)
    result = await db.execute(
        select(OnlineUserSession).where(
            OnlineUserSession.id == session_id,
            OnlineUserSession.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="ONLINE_SESSION_NOT_FOUND")
    if row.status == "ended":
        return _online_session_response(row)

    actor = dict(user)
    actor.setdefault("tenant_id", "default")
    event = await audit_service.create_event(
        AuditEventCreate(
            event_type="auth.session_end",
            category=AuditCategory.auth,
            action="session.end",
            resource_type="user",
            resource_id=row.user_id,
            severity=AuditSeverity.medium,
            message="Online session ended",
            metadata={
                "online_session_id": str(row.id),
                "username": row.username,
                "client_ip": row.client_ip,
            },
        ),
        actor,
    )
    row.status = "ended"
    row.ended_at = datetime.now(UTC)
    row.ended_audit_event_id = event.id
    await db.commit()
    await db.refresh(row)
    await _add_activity_log(
        db,
        tenant_id=row.tenant_id,
        actor_id=str(user.get("id") or ""),
        actor_username=str(user.get("username") or ""),
        resource_type="user",
        resource_id=row.user_id,
        action="session_end",
        detail=f"online session {row.id} ended",
        audit_event_id=event.id,
    )
    return _online_session_response(row)
