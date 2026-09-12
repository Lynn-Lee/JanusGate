"""审计事件 API 路由。"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import (
    AuditComplianceReport,
    AuditEvent,
    AuditEventCreate,
    AuditEventList,
    AuditKind,
    AuditReportSummary,
    AuditSeverity,
    FileTransferLogCreate,
    JobLogCreate,
    OnlineSessionItem,
    OnlineSessionList,
    TypedAuditCreate,
)
from app.api.audits.service import audit_service
from app.core.database import get_read_db
from app.core.deps import current_user
from app.services.audit_types import (
    create_typed_event,
    list_typed_events,
    record_file_transfer,
    record_job_log,
)
from app.services.session_ops import list_online_sessions

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])


def require_audit_permission(permission: str, user: dict[str, Any]) -> None:
    if permission not in user.get("permissions", []):
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
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    require_audit_permission("audit:read", user)
    items, total = await audit_service.list_events(
        tenant_id=str(user["tenant_id"]),
        event_type=event_type,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)


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


@router.get("/typed", response_model=AuditEventList)
async def list_typed_audit_events(
    user: Annotated[dict[str, Any], Depends(current_user)],
    kind: AuditKind,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    """按 JumpServer 对标类型列出 hash chain 上的分类日志。"""

    require_audit_permission("audit:read", user)
    items, total = await list_typed_events(
        tenant_id=str(user["tenant_id"]),
        kind=kind,
        limit=limit,
        offset=offset,
    )
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)


@router.post("/typed", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_typed_audit_event(
    payload: TypedAuditCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    require_audit_permission("audit:write", user)
    return await create_typed_event(payload, user)


@router.post("/ftp-logs", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_file_transfer_log(
    payload: FileTransferLogCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    """SFTP 文件传输日志入库端点，写入 #t61 hash chain。"""

    require_audit_permission("audit:write", user)
    return await record_file_transfer(payload, user)


@router.post("/job-logs", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_job_log(
    payload: JobLogCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    require_audit_permission("audit:write", user)
    return await record_job_log(payload, user)


@router.get("/online-sessions", response_model=OnlineSessionList)
async def get_online_sessions(
    user: Annotated[dict[str, Any], Depends(current_user)],
    db: AsyncSession = Depends(get_read_db),
) -> OnlineSessionList:
    """当前租户在线会话快照（可变状态）；历史开关机事件走 ``user_session`` 分类日志。"""

    require_audit_permission("audit:read", user)
    rows = await list_online_sessions(db, tenant_id=str(user["tenant_id"]))
    items = [
        OnlineSessionItem(
            id=row.id,
            subject_id=row.subject_id,
            asset_id=row.asset_id,
            account_id=row.account_id,
            protocol=row.protocol,
            status=row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return OnlineSessionList(items=items, total=len(items))
