"""#t77 作业中心 API：Playbook 目录、作业、变量、立即执行与周期 tick。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.job_center_schemas import (
    JobCreate,
    JobExecutionListResponse,
    JobExecutionResponse,
    JobListResponse,
    JobResponse,
    JobRunResponse,
    JobVariableCreate,
    JobVariableListResponse,
    JobVariableResponse,
    PlaybookCreate,
    PlaybookListResponse,
    PlaybookResponse,
    SchedulerTickResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.models.automation import AutomationJobRun
from app.models.job_center import OpsJob, OpsJobVariable, OpsPlaybook
from app.services.automation_worker import AutomationJobQueue, RedisStreamClient
from app.services.job_center import (
    JOB_KIND_ADHOC,
    JOB_KIND_PLAYBOOK,
    dump_json,
    enqueue_ops_job,
    get_active_assets,
    get_runas_account,
    get_scoped_playbook,
    load_extra_vars,
    load_int_list,
    next_run_at,
    queue_job_type,
    sanitize_adhoc_command,
    sanitize_cron_expr,
    sanitize_extra_vars,
    sanitize_playbook_filename,
    sanitize_timezone,
    sanitize_variable_name,
    sanitize_variable_value,
)
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/job-center", tags=["作业中心"])

_ERROR_STATUS = {
    "PLAYBOOK_NOT_FOUND": 404,
    "JOB_NOT_FOUND": 404,
    "ASSET_NOT_FOUND": 404,
    "RUNAS_ACCOUNT_UNAVAILABLE": 404,
    "ACCOUNT_NOT_FOUND": 404,
}


def get_automation_job_queue(
    redis: RedisStreamClient = Depends(get_redis),
) -> AutomationJobQueue:
    return AutomationJobQueue(redis=redis)


@router.get("/playbooks", response_model=PlaybookListResponse)
async def list_playbooks(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> PlaybookListResponse:
    _require_automation_permission(user, "automation:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(OpsPlaybook, actor_scope).order_by(OpsPlaybook.id.asc()))
    items = [_playbook_response(row) for row in result.scalars().all()]
    return PlaybookListResponse(items=items, total=len(items))


@router.post("/playbooks", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    data: PlaybookCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> PlaybookResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    try:
        filename = sanitize_playbook_filename(data.filename)
        name = data.name.strip()
        if not name:
            raise ValueError("PLAYBOOK_NAME_INVALID")
        playbook = OpsPlaybook(
            tenant_id=actor_scope.tenant_id,
            name=name,
            filename=filename,
            description=data.description.strip(),
            is_active=True,
        )
        db.add(playbook)
        await db.commit()
        await db.refresh(playbook)
        return _playbook_response(playbook)
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobListResponse:
    _require_automation_permission(user, "automation:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(OpsJob, actor_scope).order_by(OpsJob.id.asc()))
    items = [_job_response(row) for row in result.scalars().all()]
    return JobListResponse(items=items, total=len(items))


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: JobCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    try:
        job = await _build_job(db=db, actor_scope_tenant=actor_scope.tenant_id, user=user, data=data)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return _job_response(job)
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobResponse:
    _require_automation_permission(user, "automation:read")
    job = await _get_scoped_job(db, user, job_id)
    return _job_response(job)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_automation_permission(user, "automation:write")
    job = await _get_scoped_job(db, user, job_id)
    await db.execute(delete(OpsJobVariable).where(OpsJobVariable.job_id == job.id))
    await db.delete(job)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/jobs/{job_id}/variables", response_model=JobVariableListResponse)
async def list_job_variables(
    job_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobVariableListResponse:
    _require_automation_permission(user, "automation:read")
    job = await _get_scoped_job(db, user, job_id)
    result = await db.execute(
        select(OpsJobVariable)
        .where(OpsJobVariable.job_id == job.id)
        .where(OpsJobVariable.tenant_id == job.tenant_id)
        .order_by(OpsJobVariable.id.asc())
    )
    items = [_variable_response(row) for row in result.scalars().all()]
    return JobVariableListResponse(items=items, total=len(items))


@router.post(
    "/jobs/{job_id}/variables",
    response_model=JobVariableResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_job_variable(
    job_id: int,
    data: JobVariableCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobVariableResponse:
    _require_automation_permission(user, "automation:write")
    job = await _get_scoped_job(db, user, job_id)
    try:
        variable = OpsJobVariable(
            tenant_id=job.tenant_id,
            job_id=job.id,
            name=sanitize_variable_name(data.name),
            value=sanitize_variable_value(data.value),
        )
        db.add(variable)
        await db.commit()
        await db.refresh(variable)
        return _variable_response(variable)
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/jobs/{job_id}/run",
    response_model=JobRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> JobRunResponse:
    _require_automation_permission(user, "automation:write")
    job = await _get_scoped_job(db, user, job_id)
    try:
        message_id = await enqueue_ops_job(queue=queue, job=job, requested_by=str(user["id"]))
        return JobRunResponse(
            job_id=message_id,
            job_type=queue_job_type(job.job_kind),
            status="queued",
            ops_job_id=job.id,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc


@router.get("/executions", response_model=JobExecutionListResponse)
async def list_executions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobExecutionListResponse:
    _require_automation_permission(user, "automation:read")
    tenant_id = str(user.get("tenant_id") or "default")
    total_result = await db.execute(
        select(func.count())
        .select_from(AutomationJobRun)
        .where(AutomationJobRun.tenant_id == tenant_id)
        .where(AutomationJobRun.ops_job_id.is_not(None))
    )
    result = await db.execute(
        select(AutomationJobRun)
        .where(AutomationJobRun.tenant_id == tenant_id)
        .where(AutomationJobRun.ops_job_id.is_not(None))
        .order_by(AutomationJobRun.created_at.desc(), AutomationJobRun.message_id.desc())
        .limit(100)
    )
    return JobExecutionListResponse(
        items=[_execution_response(row) for row in result.scalars().all()],
        total=total_result.scalar_one(),
    )


@router.post("/scheduler/tick", response_model=SchedulerTickResponse)
async def tick_scheduler(
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> SchedulerTickResponse:
    """按当前租户扫描到期周期作业并 JSON-only 入队。"""

    _require_automation_permission(user, "automation:write")
    tenant_id = str(user.get("tenant_id") or "default")
    now = datetime.now(UTC)
    result = await db.execute(
        select(OpsJob)
        .where(OpsJob.tenant_id == tenant_id)
        .where(OpsJob.enabled.is_(True))
        .where(OpsJob.cron_expr.is_not(None))
        .where(OpsJob.next_run_at.is_not(None))
        .where(OpsJob.next_run_at <= now)
        .order_by(OpsJob.next_run_at.asc(), OpsJob.id.asc())
    )
    enqueued = 0
    skipped = 0
    try:
        for job in result.scalars().all():
            cron_expr = sanitize_cron_expr(job.cron_expr)
            if cron_expr is None:
                skipped += 1
                continue
            await enqueue_ops_job(queue=queue, job=job, requested_by="scheduler")
            job.next_run_at = next_run_at(cron_expr=cron_expr, timezone=job.timezone, after=now)
            enqueued += 1
    except ValueError as exc:
        raise _http_error(exc) from exc
    await db.commit()
    return SchedulerTickResponse(enqueued=enqueued, skipped=skipped)


async def _build_job(
    *,
    db: AsyncSession,
    actor_scope_tenant: str,
    user: dict[str, Any],
    data: JobCreate,
) -> OpsJob:
    name = data.name.strip()
    if not name:
        raise ValueError("JOB_NAME_INVALID")
    if any(item <= 0 or isinstance(item, bool) for item in data.target_asset_ids):
        raise ValueError("TARGET_ASSETS_INVALID")
    await get_active_assets(db, tenant_id=actor_scope_tenant, asset_ids=list(data.target_asset_ids))
    await get_runas_account(db, tenant_id=actor_scope_tenant, account_id=data.runas_account_id)
    extra_vars = sanitize_extra_vars(data.extra_vars)
    cron_expr = sanitize_cron_expr(data.cron_expr)
    timezone = sanitize_timezone(data.timezone)
    playbook_id: int | None = None
    adhoc_module: str | None = None
    adhoc_command: str | None = None
    if data.job_kind == JOB_KIND_PLAYBOOK:
        if data.playbook_id is None:
            raise ValueError("PLAYBOOK_REQUIRED")
        await get_scoped_playbook(db, tenant_id=actor_scope_tenant, playbook_id=data.playbook_id)
        playbook_id = data.playbook_id
        queue_job_type(JOB_KIND_PLAYBOOK)
    elif data.job_kind == JOB_KIND_ADHOC:
        if data.adhoc_module not in {"shell", "command"}:
            raise ValueError("ADHOC_MODULE_INVALID")
        adhoc_module = data.adhoc_module
        adhoc_command = sanitize_adhoc_command(data.adhoc_command or "")
        queue_job_type(JOB_KIND_ADHOC)
    else:
        raise ValueError("UNSUPPORTED_JOB_KIND")
    now = datetime.now(UTC)
    upcoming = (
        next_run_at(cron_expr=cron_expr, timezone=timezone, after=now) if cron_expr else None
    )
    return OpsJob(
        tenant_id=actor_scope_tenant,
        name=name,
        job_kind=data.job_kind,
        playbook_id=playbook_id,
        adhoc_module=adhoc_module,
        adhoc_command=adhoc_command,
        extra_vars_json=dump_json(extra_vars),
        target_asset_ids_json=dump_json(list(data.target_asset_ids)),
        runas_account_id=data.runas_account_id,
        cron_expr=cron_expr,
        timezone=timezone,
        enabled=data.enabled,
        next_run_at=upcoming,
        created_by=str(user["id"]),
    )


async def _get_scoped_job(db: AsyncSession, user: dict[str, Any], job_id: int) -> OpsJob:
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(OpsJob, actor_scope).where(OpsJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return job


def _playbook_response(row: OpsPlaybook) -> PlaybookResponse:
    return PlaybookResponse(
        id=row.id,
        name=row.name,
        filename=row.filename,
        description=row.description,
        is_active=row.is_active,
    )


def _job_response(row: OpsJob) -> JobResponse:
    extra_vars = load_extra_vars(row.extra_vars_json)
    return JobResponse(
        id=row.id,
        name=row.name,
        job_kind=row.job_kind,
        playbook_id=row.playbook_id,
        adhoc_module=row.adhoc_module,
        adhoc_command=row.adhoc_command,
        extra_vars=extra_vars,
        extra_var_keys=sorted(extra_vars.keys()),
        target_asset_ids=load_int_list(row.target_asset_ids_json),
        runas_account_id=row.runas_account_id,
        cron_expr=row.cron_expr,
        timezone=row.timezone,
        enabled=row.enabled,
        next_run_at=row.next_run_at,
        created_by=row.created_by,
    )


def _variable_response(row: OpsJobVariable) -> JobVariableResponse:
    return JobVariableResponse(id=row.id, job_id=row.job_id, name=row.name, value=row.value)


def _execution_response(row: AutomationJobRun) -> JobExecutionResponse:
    keys: list[str] = []
    if row.extra_var_keys:
        parsed = json.loads(row.extra_var_keys)
        if isinstance(parsed, list):
            keys = [str(item) for item in parsed]
    return JobExecutionResponse(
        message_id=row.message_id,
        job_type=row.job_type,
        status=row.status,
        requested_by=row.requested_by,
        ops_job_id=row.ops_job_id,
        job_kind=row.job_kind,
        playbook_name=row.playbook_name,
        runas_account_id=row.runas_account_id,
        extra_var_keys=keys,
        target_count=row.target_count,
        error_code=row.error_code,
    )


def _require_automation_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _http_error(exc: ValueError) -> HTTPException:
    code = str(exc.args[0]) if exc.args else "JOB_CENTER_INVALID"
    status_code = _ERROR_STATUS.get(code, 400)
    return HTTPException(status_code=status_code, detail=code)
