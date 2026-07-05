"""审计事件 API 路由。"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.audits.schemas import (
    AuditComplianceReport,
    AuditEvent,
    AuditEventCreate,
    AuditEventList,
    AuditReportSummary,
    AuditSeverity,
)
from app.api.audits.service import audit_service
from app.core.deps import current_user

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
def list_audit_events(
    user: Annotated[dict[str, Any], Depends(current_user)],
    event_type: Annotated[str | None, Query(min_length=3, max_length=120)] = None,
    severity: AuditSeverity | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventList:
    require_audit_permission("audit:read", user)
    items, total = audit_service.list_events(
        tenant_id=str(user["tenant_id"]),
        event_type=event_type,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return AuditEventList(items=items, total=total, limit=limit, offset=offset)


@router.get("/reports/summary", response_model=AuditReportSummary)
def get_audit_report_summary(
    user: Annotated[dict[str, Any], Depends(current_user)],
) -> AuditReportSummary:
    require_audit_permission("audit:read", user)
    return audit_service.report_summary(tenant_id=str(user["tenant_id"]))


@router.get("/reports/compliance", response_model=AuditComplianceReport)
def get_audit_compliance_report(
    user: Annotated[dict[str, Any], Depends(current_user)],
    template: Annotated[str, Query(min_length=3, max_length=80)] = "soc2-access",
) -> AuditComplianceReport:
    require_audit_permission("audit:read", user)
    return audit_service.compliance_report(tenant_id=str(user["tenant_id"]), template=template)
