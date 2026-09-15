"""#t77 作业中心 API：Playbook / Variable / Job / 执行记录 / 周期 tick。

权限沿用 automation:read / automation:write。失败返回稳定大写错误码。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.job_center_schemas import (
    JobCreate,
    JobExecutionListResponse,
    JobExecutionResponse,
    JobListResponse,
    JobPlaybookCreate,
    JobPlaybookListResponse,
    JobPlaybookResponse,
    JobResponse,
    JobRunResponse,
    JobVariableCreate,
    JobVariableListResponse,
    JobVariableResponse,
    SchedulerTickResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.models.job_center import Job, JobExecution, JobPlaybook, JobVariable
from app.services.automation_worker import AutomationJobQueue, RedisStreamClient
from app.services.job_center import (
    ADHOC_JOB_TYPE,
    JOB_KIND_ADHOC,
    JOB_KIND_PLAYBOOK,
    coerce_extra_vars,
    enqueue_job,
    ensure_active_assets,
    next_run_at_for_interval,
    resolve_extra_vars,
    resolve_runas_account,
    tick_due_jobs,
    validate_adhoc_command,
    validate_interval_seconds,
    validate_playbook_content,
    validate_playbook_filename,
    validate_variable_name,
)
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/job-center", tags=["作业中心"])


def get_automation_job_queue(
    redis: RedisStreamClient = Depends(get_redis),
) -> AutomationJobQueue:
    return AutomationJobQueue(redis=redis)


def _require_automation_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


@router.get("/playbooks/", response_model=JobPlaybookListResponse)
async def list_job_playbooks(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobPlaybookListResponse:
    _require_automation_permission(user, "automation:read")
    result = await db.execute(
        scoped_select(JobPlaybook, actor_scope_from_user(user)).order_by(JobPlaybook.id.asc())
    )
    items = [_playbook_response(row) for row in result.scalars().all()]
    return JobPlaybookListResponse(items=items, total=len(items))


@router.post("/playbooks/", response_model=JobPlaybookResponse, status_code=status.HTTP_201_CREATED)
async def create_job_playbook(
    data: JobPlaybookCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobPlaybookResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    playbook = JobPlaybook(
        tenant_id=actor_scope.tenant_id,
        name=data.name.strip(),
        filename=validate_playbook_filename(data.filename),
        content=validate_playbook_content(data.content),
    )
    db.add(playbook)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="JOB_PLAYBOOK_NAME_CONFLICT") from exc
    await db.refresh(playbook)
    return _playbook_response(playbook)


@router.get("/variables/", response_model=JobVariableListResponse)
async def list_job_variables(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobVariableListResponse:
    _require_automation_permission(user, "automation:read")
    result = await db.execute(
        scoped_select(JobVariable, actor_scope_from_user(user)).order_by(JobVariable.id.asc())
    )
    items = [_variable_response(row) for row in result.scalars().all()]
    return JobVariableListResponse(items=items, total=len(items))


@router.post("/variables/", response_model=JobVariableResponse, status_code=status.HTTP_201_CREATED)
async def create_job_variable(
    data: JobVariableCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobVariableResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    variable = JobVariable(
        tenant_id=actor_scope.tenant_id,
        name=validate_variable_name(data.name),
        extra_vars=coerce_extra_vars(data.extra_vars),
    )
    db.add(variable)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="JOB_VARIABLE_NAME_CONFLICT") from exc
    await db.refresh(variable)
    return _variable_response(variable)


@router.get("/jobs/", response_model=JobListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobListResponse:
    _require_automation_permission(user, "automation:read")
    result = await db.execute(
        scoped_select(Job, actor_scope_from_user(user)).order_by(Job.id.asc())
    )
    items = [_job_response(row) for row in result.scalars().all()]
    return JobListResponse(items=items, total=len(items))


@router.post("/jobs/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: JobCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    target_asset_ids = await ensure_active_assets(
        db,
        tenant_id=actor_scope.tenant_id,
        asset_ids=list(data.target_asset_ids),
    )
    extra_var_names, _extra_vars = await resolve_extra_vars(
        db,
        tenant_id=actor_scope.tenant_id,
        names=list(data.extra_var_names),
    )
    runas_account_id = await resolve_runas_account(
        db,
        tenant_id=actor_scope.tenant_id,
        account_id=data.runas_account_id,
    )
    interval_seconds = validate_interval_seconds(data.interval_seconds)
    playbook_id: int | None = None
    adhoc_module = "command"
    adhoc_args = ""
    if data.kind == JOB_KIND_PLAYBOOK:
        if data.playbook_id is None:
            raise HTTPException(status_code=400, detail="JOB_PLAYBOOK_REQUIRED")
        playbook_result = await db.execute(
            scoped_select(JobPlaybook, actor_scope).where(JobPlaybook.id == data.playbook_id)
        )
        playbook = playbook_result.scalar_one_or_none()
        if playbook is None:
            raise HTTPException(status_code=400, detail="JOB_PLAYBOOK_NOT_FOUND")
        playbook_id = playbook.id
    else:
        adhoc_module, adhoc_args = validate_adhoc_command(
            module=data.adhoc_module,
            args=data.adhoc_args,
        )
    job = Job(
        tenant_id=actor_scope.tenant_id,
        name=data.name.strip(),
        kind=data.kind,
        playbook_id=playbook_id,
        adhoc_module=adhoc_module,
        adhoc_args=adhoc_args,
        target_asset_ids=target_asset_ids,
        extra_var_names=extra_var_names,
        runas_account_id=runas_account_id,
        interval_seconds=interval_seconds,
        next_run_at=next_run_at_for_interval(interval_seconds),
        enabled=data.enabled,
        check_mode=data.check_mode,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="JOB_NAME_CONFLICT") from exc
    await db.refresh(job)
    return _job_response(job)


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
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(Job, actor_scope).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    execution = await enqueue_job(
        session=db,
        queue=queue,
        job=job,
        requested_by=str(user["id"]),
    )
    await db.commit()
    job_type = "ansible.playbook" if job.kind == JOB_KIND_PLAYBOOK else ADHOC_JOB_TYPE
    return JobRunResponse(
        job_id=job.id,
        message_id=execution.message_id,
        job_type=job_type,
        status=execution.status,
    )


@router.get("/executions/", response_model=JobExecutionListResponse)
async def list_job_executions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobExecutionListResponse:
    _require_automation_permission(user, "automation:read")
    result = await db.execute(
        scoped_select(JobExecution, actor_scope_from_user(user))
        .order_by(JobExecution.queued_at.desc(), JobExecution.id.desc())
        .limit(100)
    )
    items = [_execution_response(row) for row in result.scalars().all()]
    return JobExecutionListResponse(items=items, total=len(items))


@router.post(
    "/scheduler/tick",
    response_model=SchedulerTickResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def tick_job_scheduler(
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> SchedulerTickResponse:
    """手动触发周期作业拾取；生产可由调度器周期调用同一入口。"""

    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    executions = await tick_due_jobs(
        session=db,
        queue=queue,
        tenant_id=actor_scope.tenant_id,
        requested_by=str(user["id"]),
    )
    await db.commit()
    items: list[JobRunResponse] = []
    for execution in executions:
        job = await db.get(Job, execution.job_id)
        job_type = "ansible.playbook"
        if job is not None and job.kind == JOB_KIND_ADHOC:
            job_type = ADHOC_JOB_TYPE
        items.append(
            JobRunResponse(
                job_id=execution.job_id,
                message_id=execution.message_id,
                job_type=job_type,
                status=execution.status,
            )
        )
    return SchedulerTickResponse(queued=len(items), items=items)


def _playbook_response(playbook: JobPlaybook) -> JobPlaybookResponse:
    return JobPlaybookResponse(
        id=playbook.id,
        name=playbook.name,
        filename=playbook.filename,
        content=playbook.content,
    )


def _variable_response(variable: JobVariable) -> JobVariableResponse:
    return JobVariableResponse(
        id=variable.id,
        name=variable.name,
        extra_vars=dict(variable.extra_vars or {}),
    )


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        name=job.name,
        kind=job.kind,
        playbook_id=job.playbook_id,
        adhoc_module=job.adhoc_module,
        adhoc_args=job.adhoc_args,
        target_asset_ids=list(job.target_asset_ids or []),
        extra_var_names=list(job.extra_var_names or []),
        runas_account_id=job.runas_account_id,
        interval_seconds=job.interval_seconds,
        next_run_at=job.next_run_at,
        enabled=job.enabled,
        check_mode=job.check_mode,
    )


def _execution_response(execution: JobExecution) -> JobExecutionResponse:
    return JobExecutionResponse(
        id=execution.id,
        job_id=execution.job_id,
        message_id=execution.message_id,
        status=execution.status,
        requested_by=execution.requested_by,
        error_code=execution.error_code,
        queued_at=execution.queued_at,
    )
