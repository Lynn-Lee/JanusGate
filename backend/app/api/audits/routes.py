"""审计事件 API 路由。"""
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import (
    AuditCategory,
    AuditComplianceReport,
    AuditEvent,
    AuditEventCreate,
    AuditEventList,
    AuditReportSummary,
    AuditSeverity,
    OnlineSession,
    OnlineSessionList,
    TypedAuditLogCreate,
)
from app.api.audits.service import audit_service
from app.api.audits.typed import TYPED_LIST_CATEGORIES, record_typed_event
from app.api.sessions.service import SessionStatus
from app.core.database import get_read_db
from app.core.deps import current_user
from app.models.session import SessionModel

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])

_ONLINE_SESSION_STATUSES = (
    SessionStatus.REQUESTED.value,
    SessionStatus.AUTHORIZED.value,
    SessionStatus.CONNECTING.value,
    SessionStatus.ACTIVE.value,
    SessionStatus.CLOSING.value,
)


def require_audit_permission(permission: str, user: dict[str, Any]) -> None:
    if permission not in user.get("permissions", []) and "admin" not in user.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限: {permission}")


@router.post("/events", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_audit_event(
    payload: AuditEventCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    require_audit_permission("audit:write", user)
    return await audit_service.create_event(payload, user)


@router.get("/events", response_model=AuditEventList)
async def list_audit_events(
    user: Annotated[dict[str, Any], Depends(current_user)],
    event_type: Annotated[str | None, Query(min_length=3, max_length=120)] = None,
    severity: AuditSeverity | None = None,
    category: AuditCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    require_audit_permission("audit:read", user)
    items, total = await audit_service.list_events(
        tenant_id=str(user["tenant_id"]),
        event_type=event_type,
        severity=severity,
        categories=[category.value] if category is not None else None,
        limit=limit,
        offset=offset,
    )
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)


@router.post("/operate-logs", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_operate_log(
    payload: TypedAuditLogCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    require_audit_permission("audit:write", user)
    return await record_typed_event(
        category=AuditCategory.operate, payload=payload, actor=user
    )


@router.post("/activity-logs", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_activity_log(
    payload: TypedAuditLogCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    require_audit_permission("audit:write", user)
    return await record_typed_event(
        category=AuditCategory.activity, payload=payload, actor=user
    )


@router.get("/operate-logs", response_model=AuditEventList)
async def list_operate_logs(
    user: Annotated[dict[str, Any], Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    return await _list_typed_logs(user, kind="operate", limit=limit, offset=offset)


@router.get("/activity-logs", response_model=AuditEventList)
async def list_activity_logs(
    user: Annotated[dict[str, Any], Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    return await _list_typed_logs(user, kind="activity", limit=limit, offset=offset)


@router.get("/file-transfers", response_model=AuditEventList)
async def list_file_transfers(
    user: Annotated[dict[str, Any], Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    return await _list_typed_logs(user, kind="file_transfer", limit=limit, offset=offset)


@router.get("/password-changes", response_model=AuditEventList)
async def list_password_changes(
    user: Annotated[dict[str, Any], Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    return await _list_typed_logs(user, kind="password_change", limit=limit, offset=offset)


@router.get("/job-logs", response_model=AuditEventList)
async def list_job_logs(
    user: Annotated[dict[str, Any], Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    return await _list_typed_logs(user, kind="job", limit=limit, offset=offset)


@router.get("/online-sessions", response_model=OnlineSessionList)
async def list_online_sessions(
    user: Annotated[dict[str, Any], Depends(current_user)],
    db: AsyncSession = Depends(get_read_db),
) -> OnlineSessionList:
    """列出当前租户未关闭的 PAM 会话；不返回 connection_url / token。"""

    require_audit_permission("audit:read", user)
    tenant_id = str(user["tenant_id"])
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.tenant_id == tenant_id)
        .where(SessionModel.status.in_(_ONLINE_SESSION_STATUSES))
        .order_by(SessionModel.updated_at.desc())
    )
    items = [
        OnlineSession(
            id=model.id,
            subject_id=model.subject_id,
            asset_id=model.asset_id,
            account_id=model.account_id,
            protocol=model.protocol,
            status=model.status,
            client_ip=model.client_ip,
            created_at=_as_utc(model.created_at),
            updated_at=_as_utc(model.updated_at),
        )
        for model in result.scalars().all()
    ]
    return OnlineSessionList(items=items, total=len(items))


@router.get("/reports/summary", response_model=AuditReportSummary)
async def get_audit_report_summary(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditReportSummary:
    require_audit_permission("audit:read", user)
    return await audit_service.report_summary(tenant_id=str(user["tenant_id"]))


@router.get("/reports/compliance", response_model=AuditComplianceReport)
async def get_audit_compliance_report(
    user: Annotated[dict[str, Any], Depends(current_user)],
    template: Annotated[str, Query(min_length=3, max_length=80)] = "soc2-access",
) -> AuditComplianceReport:
    require_audit_permission("audit:read", user)
    return await audit_service.compliance_report(
        tenant_id=str(user["tenant_id"]), template=template
    )


async def _list_typed_logs(
    user: dict[str, Any], *, kind: str, limit: int, offset: int
) -> AuditEventList:
    require_audit_permission("audit:read", user)
    items, total = await audit_service.list_events(
        tenant_id=str(user["tenant_id"]),
        event_type=None,
        severity=None,
        categories=list(TYPED_LIST_CATEGORIES[kind]),
        limit=limit,
        offset=offset,
    )
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
