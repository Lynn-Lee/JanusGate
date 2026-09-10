"""审计事件 API 路由。"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.audits.schemas import (
    AuditCategory,
    AuditComplianceReport,
    AuditEvent,
    AuditEventCreate,
    AuditEventList,
    AuditReportSummary,
    AuditSeverity,
)
from app.api.audits.service import audit_service
from app.core.deps import current_user
from app.services.typed_audit import TYPED_AUDIT_LOGS

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])


class TypedAuditCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_kind: str = Field(min_length=3, max_length=32)
    action: str = Field(min_length=1, max_length=120)
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(min_length=1, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FtpLogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=120)
    asset_id: str = Field(min_length=1, max_length=120)
    remote_path: str = Field(min_length=1, max_length=500)
    direction: str = Field(pattern="^(upload|download)$")
    size_bytes: int = Field(ge=0)
    sha256: str = Field(default="", max_length=64)
    status: str = Field(pattern="^(success|failed)$")
    error_code: str = Field(default="", max_length=120)


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


@router.post("/typed", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_typed_audit_event(
    payload: TypedAuditCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    """按分类写入操作/活动/改密/在线会话/作业等日志，并入 hash chain。"""

    require_audit_permission("audit:write", user)
    mapping = TYPED_AUDIT_LOGS.get(payload.log_kind)
    if mapping is None:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_AUDIT_LOG_KIND")
    event_type, category = mapping
    return await audit_service.create_event(
        AuditEventCreate(
            event_type=event_type,
            category=category,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            session_id=payload.session_id,
            severity=AuditSeverity.medium,
            message=payload.message,
            metadata=payload.metadata,
        ),
        user,
    )


@router.post("/ftp-logs", response_model=AuditEvent, status_code=status.HTTP_201_CREATED)
async def create_ftp_log(
    payload: FtpLogCreate,
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditEvent:
    """SFTP 文件传输日志入库端点；事件进入 #t61 hash chain。"""

    require_audit_permission("audit:write", user)
    return await audit_service.create_event(
        AuditEventCreate(
            event_type="ftp.transfer",
            category=AuditCategory.ftp,
            action=payload.direction,
            resource_type="file_transfer",
            resource_id=payload.remote_path[:120],
            session_id=payload.session_id,
            severity=AuditSeverity.medium if payload.status == "failed" else AuditSeverity.low,
            message=f"sftp {payload.direction} {payload.status}",
            metadata={
                "asset_id": payload.asset_id,
                "size_bytes": payload.size_bytes,
                "sha256": payload.sha256,
                "status": payload.status,
                "error_code": payload.error_code,
            },
        ),
        user,
    )


@router.get("/events", response_model=AuditEventList)
async def list_audit_events(
    user: Annotated[dict[str, Any], Depends(current_user)],
    event_type: Annotated[str | None, Query(min_length=3, max_length=120)] = None,
    category: Annotated[str | None, Query(min_length=3, max_length=32)] = None,
    severity: AuditSeverity | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    require_audit_permission("audit:read", user)
    items, total = await audit_service.list_events(
        tenant_id=str(user["tenant_id"]),
        event_type=event_type,
        category=category,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)


@router.get("/typed/{log_kind}", response_model=AuditEventList)
async def list_typed_audit_events(
    log_kind: str,
    user: Annotated[dict[str, Any], Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    require_audit_permission("audit:read", user)
    mapping = TYPED_AUDIT_LOGS.get(log_kind)
    if mapping is None:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_AUDIT_LOG_KIND")
    event_type, _category = mapping
    items, total = await audit_service.list_events(
        tenant_id=str(user["tenant_id"]),
        event_type=event_type,
        severity=None,
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
