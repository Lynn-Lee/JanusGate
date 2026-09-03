"""#t73 账号模板、风险与自动化执行记录 API。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account_automation_schemas import (
    AccountAutomationRunListResponse,
    AccountAutomationRunResponse,
    AccountRiskListResponse,
    AccountRiskResolveRequest,
    AccountRiskResponse,
    AccountTemplateCreate,
    AccountTemplateListResponse,
    AccountTemplateResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.account import AccountAutomationRun, AccountRisk, AccountTemplate
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(tags=["账号自动化"])


@router.get("/account-templates/", response_model=AccountTemplateListResponse)
async def list_account_templates(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountTemplateListResponse:
    _require(user, "accounts:read")
    result = await db.execute(
        scoped_select(AccountTemplate, actor_scope_from_user(user)).order_by(AccountTemplate.id)
    )
    items = [_template_response(row) for row in result.scalars().all()]
    return AccountTemplateListResponse(items=items, total=len(items))


@router.post(
    "/account-templates/",
    response_model=AccountTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_account_template(
    data: AccountTemplateCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountTemplateResponse:
    _require(user, "accounts:write")
    tenant_id = str(user.get("tenant_id") or "default")
    template = AccountTemplate(
        tenant_id=tenant_id,
        name=data.name,
        username=data.username,
        protocol=data.protocol,
        privileged=data.privileged,
        shell=data.shell,
        home_dir=data.home_dir,
        groups_json=json.dumps(data.groups, ensure_ascii=False),
        organization_id=data.organization_id,
        team_id=data.team_id,
        project_id=data.project_id,
        status=data.status,
    )
    db.add(template)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="ACCOUNT_TEMPLATE_CREATE_FAILED") from exc
    await db.refresh(template)
    return _template_response(template)


@router.get("/account-risks/", response_model=AccountRiskListResponse)
async def list_account_risks(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
    status_filter: str | None = None,
) -> AccountRiskListResponse:
    _require(user, "accounts:read")
    tenant_id = str(user.get("tenant_id") or "default")
    stmt = select(AccountRisk).where(AccountRisk.tenant_id == tenant_id)
    if status_filter:
        stmt = stmt.where(AccountRisk.status == status_filter)
    stmt = stmt.order_by(AccountRisk.id.desc())
    result = await db.execute(stmt)
    items = [_risk_response(row) for row in result.scalars().all()]
    return AccountRiskListResponse(items=items, total=len(items))


@router.post("/account-risks/{risk_id}/resolve", response_model=AccountRiskResponse)
async def resolve_account_risk(
    risk_id: int,
    data: AccountRiskResolveRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountRiskResponse:
    _require(user, "accounts:automate")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(AccountRisk)
        .where(AccountRisk.id == risk_id)
        .where(AccountRisk.tenant_id == tenant_id)
    )
    risk = result.scalar_one_or_none()
    if risk is None:
        raise HTTPException(status_code=404, detail="ACCOUNT_RISK_NOT_FOUND")
    risk.status = data.status
    await db.commit()
    await db.refresh(risk)
    return _risk_response(risk)


@router.get("/account-automation/runs", response_model=AccountAutomationRunListResponse)
async def list_account_automation_runs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountAutomationRunListResponse:
    _require(user, "accounts:automate")
    tenant_id = str(user.get("tenant_id") or "default")
    total_result = await db.execute(
        select(func.count())
        .select_from(AccountAutomationRun)
        .where(AccountAutomationRun.tenant_id == tenant_id)
    )
    result = await db.execute(
        select(AccountAutomationRun)
        .where(AccountAutomationRun.tenant_id == tenant_id)
        .order_by(AccountAutomationRun.id.desc())
        .limit(100)
    )
    runs = result.scalars().all()
    return AccountAutomationRunListResponse(
        items=[_run_response(run) for run in runs],
        total=total_result.scalar_one(),
    )


def _require(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _template_response(template: AccountTemplate) -> AccountTemplateResponse:
    try:
        groups_raw = json.loads(template.groups_json)
        groups = [str(item) for item in groups_raw] if isinstance(groups_raw, list) else []
    except json.JSONDecodeError:
        groups = []
    return AccountTemplateResponse(
        id=template.id,
        tenant_id=template.tenant_id,
        name=template.name,
        username=template.username,
        protocol=template.protocol,
        privileged=template.privileged,
        shell=template.shell,
        home_dir=template.home_dir,
        groups=groups,
        organization_id=template.organization_id,
        team_id=template.team_id,
        project_id=template.project_id,
        status=template.status,
    )


def _risk_response(risk: AccountRisk) -> AccountRiskResponse:
    return AccountRiskResponse(
        id=risk.id,
        tenant_id=risk.tenant_id,
        asset_id=risk.asset_id,
        account_id=risk.account_id,
        username=risk.username,
        risk_type=risk.risk_type,
        severity=risk.severity,
        detail=risk.detail,
        status=risk.status,
        source_job_type=risk.source_job_type,
        created_at=risk.created_at,
    )


def _run_response(run: AccountAutomationRun) -> AccountAutomationRunResponse:
    return AccountAutomationRunResponse(
        id=run.id,
        message_id=run.message_id,
        job_type=run.job_type,
        status=run.status,
        requested_by=run.requested_by,
        account_id=run.account_id,
        asset_id=run.asset_id,
        template_id=run.template_id,
        result_summary=run.result_summary,
        error_code=run.error_code,
    )
