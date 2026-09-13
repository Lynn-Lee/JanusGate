"""作业中心 API：Playbook、作业、临时命令与执行记录。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.automation import get_automation_job_queue
from app.api.ops_schemas import (
    OpsAdhocCreate,
    OpsExecutionListResponse,
    OpsExecutionResponse,
    OpsJobCreate,
    OpsJobListResponse,
    OpsJobResponse,
    OpsJobRunRequest,
    OpsPlaybookCreate,
    OpsPlaybookListResponse,
    OpsPlaybookResponse,
    OpsTickResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.ops import OpsJob, OpsJobExecution, OpsPlaybook
from app.services.automation_worker import AutomationJobQueue
from app.services.ops_jobs import OpsJobService
from app.services.ops_sanitize import OpsConfigError, loads_ids, loads_vars

router = APIRouter(prefix="/ops", tags=["作业中心"])


def _require_ops_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _http_ops_error(exc: OpsConfigError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _playbook_response(row: OpsPlaybook) -> OpsPlaybookResponse:
    return OpsPlaybookResponse(
        id=row.id,
        name=row.name,
        relative_path=row.relative_path,
        description=row.description,
        status=row.status,
    )


def _job_response(row: OpsJob) -> OpsJobResponse:
    return OpsJobResponse(
        id=row.id,
        name=row.name,
        job_type=row.job_type,
        playbook_id=row.playbook_id,
        adhoc_module=row.adhoc_module,
        command=row.command,
        target_asset_ids=loads_ids(row.target_asset_ids_json),
        extra_vars=loads_vars(row.extra_vars_json),
        runas_account_id=row.runas_account_id,
        cron_expr=row.cron_expr,
        next_run_at=row.next_run_at,
        check_mode=row.check_mode,
        status=row.status,
    )


def _execution_response(row: OpsJobExecution) -> OpsExecutionResponse:
    return OpsExecutionResponse(
        id=row.id,
        job_id=row.job_id,
        message_id=row.message_id,
        job_type=row.job_type,
        playbook_name=row.playbook_name,
        command=row.command,
        extra_vars=loads_vars(row.extra_vars_json),
        target_asset_ids=loads_ids(row.target_asset_ids_json),
        check_mode=row.check_mode,
        status=row.status,
        error_code=row.error_code,
        requested_by=row.requested_by,
        created_at=row.created_at,
    )


@router.get("/playbooks", response_model=OpsPlaybookListResponse)
async def list_playbooks(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> OpsPlaybookListResponse:
    """列出当前租户已登记的官方 Playbook。"""
    _require_ops_permission(user, "automation:read")
    tenant_id = str(user.get("tenant_id") or "default")
    total = (
        await db.execute(
            select(func.count()).select_from(OpsPlaybook).where(OpsPlaybook.tenant_id == tenant_id)
        )
    ).scalar_one()
    rows = list(
        (
            await db.execute(
                select(OpsPlaybook)
                .where(OpsPlaybook.tenant_id == tenant_id)
                .order_by(OpsPlaybook.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return OpsPlaybookListResponse(items=[_playbook_response(row) for row in rows], total=total)


@router.post("/playbooks", response_model=OpsPlaybookResponse, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    payload: OpsPlaybookCreate,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> OpsPlaybookResponse:
    """登记官方目录内的 Playbook，路径必须经过消毒。"""
    _require_ops_permission(user, "automation:write")
    try:
        row = await OpsJobService(db, queue).create_playbook(
            user=user,
            name=payload.name,
            relative_path=payload.relative_path,
            description=payload.description,
        )
    except OpsConfigError as exc:
        raise _http_ops_error(exc) from exc
    return _playbook_response(row)


@router.get("/jobs", response_model=OpsJobListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> OpsJobListResponse:
    """列出当前租户作业定义。"""
    _require_ops_permission(user, "automation:read")
    tenant_id = str(user.get("tenant_id") or "default")
    total = (
        await db.execute(select(func.count()).select_from(OpsJob).where(OpsJob.tenant_id == tenant_id))
    ).scalar_one()
    rows = list(
        (
            await db.execute(
                select(OpsJob).where(OpsJob.tenant_id == tenant_id).order_by(OpsJob.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return OpsJobListResponse(items=[_job_response(row) for row in rows], total=total)


@router.post("/jobs", response_model=OpsJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: OpsJobCreate,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> OpsJobResponse:
    """创建 Playbook 作业或临时命令作业。"""
    _require_ops_permission(user, "automation:write")
    try:
        job = await OpsJobService(db, queue).create_job(
            user=user,
            name=payload.name,
            job_type=payload.job_type,
            runas_account_id=payload.runas_account_id,
            target_asset_ids=payload.target_asset_ids,
            playbook_id=payload.playbook_id,
            adhoc_module_name=payload.adhoc_module,
            command=payload.command,
            extra=payload.extra_vars,
            variables=[item.model_dump() for item in payload.variables],
            cron=payload.cron_expr,
            check_mode=payload.check_mode,
        )
    except OpsConfigError as exc:
        raise _http_ops_error(exc) from exc
    return _job_response(job)


@router.post(
    "/jobs/{job_id}/run",
    response_model=OpsExecutionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_job(
    job_id: int,
    payload: OpsJobRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> OpsExecutionResponse:
    """立即执行作业；队列 payload 只含 execution_id 与 check_mode。"""
    _require_ops_permission(user, "automation:write")
    body = payload or OpsJobRunRequest()
    try:
        execution = await OpsJobService(db, queue).run_job(
            user=user,
            job_id=job_id,
            extra=body.extra_vars,
        )
    except OpsConfigError as extra_exc:
        raise _http_ops_error(extra_exc) from extra_exc
    return _execution_response(execution)


@router.post("/adhoc", response_model=OpsExecutionResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_adhoc(
    payload: OpsAdhocCreate,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> OpsExecutionResponse:
    """执行临时命令；入队前走命令过滤器，DENY/REVIEW 均拒绝。"""
    _require_ops_permission(user, "automation:write")
    try:
        execution = await OpsJobService(db, queue).run_adhoc(
            user=user,
            runas_account_id=payload.runas_account_id,
            target_asset_ids=payload.target_asset_ids,
            module_name=payload.adhoc_module,
            command=payload.command,
            extra=payload.extra_vars,
            check_mode=payload.check_mode,
        )
    except OpsConfigError as exc:
        raise _http_ops_error(exc) from exc
    return _execution_response(execution)


@router.get("/executions", response_model=OpsExecutionListResponse)
async def list_executions(
    job_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> OpsExecutionListResponse:
    """列出当前租户作业执行记录。"""
    _require_ops_permission(user, "automation:read")
    tenant_id = str(user.get("tenant_id") or "default")
    filters = [OpsJobExecution.tenant_id == tenant_id]
    if job_id is not None:
        filters.append(OpsJobExecution.job_id == job_id)
    total = (
        await db.execute(
            select(func.count()).select_from(OpsJobExecution).where(*filters)
        )
    ).scalar_one()
    rows = list(
        (
            await db.execute(
                select(OpsJobExecution)
                .where(*filters)
                .order_by(OpsJobExecution.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return OpsExecutionListResponse(items=[_execution_response(row) for row in rows], total=total)


@router.post("/scheduler/tick", response_model=OpsTickResponse)
async def scheduler_tick(
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> OpsTickResponse:
    """扫描当前租户到期的周期作业并入队。"""
    _require_ops_permission(user, "automation:write")
    queued = await OpsJobService(db, queue).tick(user=user)
    return OpsTickResponse(queued_execution_ids=queued)
