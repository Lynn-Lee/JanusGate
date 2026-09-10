"""#t77 作业中心 API：作业 / 临时命令 / 周期调度 / 执行记录。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.models.automation import AutomationJobRun, JobDefinition
from app.services.automation_worker import AutomationJobQueue, RedisStreamClient
from app.services.job_center import (
    dispatch_due_cron_jobs,
    enqueue_job_definition,
    get_job_definition,
    list_job_definitions,
    new_job_id,
    next_cron_run,
    parse_cron_expression,
    resolve_run_as_user_id,
    validate_extra_variables,
    validate_job_definition_payload,
)

router = APIRouter(prefix="/job-center", tags=["作业中心"])


class JobDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    job_type: str = Field(min_length=3, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    extra_variables: dict[str, Any] = Field(default_factory=dict)
    cron_expression: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    run_as_user_id: str | None = Field(default=None, max_length=64)


class JobDefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    payload: dict[str, Any] | None = None
    extra_variables: dict[str, Any] | None = None
    cron_expression: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    run_as_user_id: str | None = Field(default=None, max_length=64)


class JobRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extra_variables: dict[str, Any] = Field(default_factory=dict)


class AdhocJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    command: str = Field(min_length=1, max_length=500)
    target_asset_ids: list[int] = Field(min_length=1, max_length=200)
    extra_variables: dict[str, Any] = Field(default_factory=dict)
    run_as_user_id: str | None = Field(default=None, max_length=64)
    save: bool = False


class JobDefinitionResponse(BaseModel):
    id: str
    name: str
    job_type: str
    payload: dict[str, Any]
    extra_variables: dict[str, Any]
    cron_expression: str | None
    next_run_at: datetime | None
    enabled: bool
    run_as_user_id: str | None
    created_by: str


class JobDefinitionListResponse(BaseModel):
    items: list[JobDefinitionResponse]
    total: int


class JobRunResponse(BaseModel):
    message_id: str
    job_type: str
    status: str
    requested_by: str
    playbook_name: str | None
    job_definition_id: str | None
    extra_variables: dict[str, Any] | None
    run_as_user_id: str | None


class CronDispatchResponse(BaseModel):
    dispatched_job_ids: list[str]


def get_automation_job_queue(
    redis: RedisStreamClient = Depends(get_redis),
) -> AutomationJobQueue:
    return AutomationJobQueue(redis=redis)


@router.get("/jobs", response_model=JobDefinitionListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobDefinitionListResponse:
    _require_automation_permission(user, "automation:read")
    tenant_id = str(user.get("tenant_id") or "default")
    jobs = await list_job_definitions(db, tenant_id=tenant_id)
    return JobDefinitionListResponse(items=[_job_response(job) for job in jobs], total=len(jobs))


@router.post("/jobs", response_model=JobDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: JobDefinitionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobDefinitionResponse:
    _require_automation_permission(user, "automation:write")
    job = _build_job(data=data, user=user)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_response(job)


@router.patch("/jobs/{job_id}", response_model=JobDefinitionResponse)
async def update_job(
    job_id: str,
    data: JobDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobDefinitionResponse:
    _require_automation_permission(user, "automation:write")
    job = await _load_job(db, user=user, job_id=job_id)
    if data.name is not None:
        job.name = data.name
    if data.payload is not None:
        try:
            job.payload = validate_job_definition_payload(job_type=job.job_type, payload=data.payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data.extra_variables is not None:
        try:
            job.extra_variables = validate_extra_variables(data.extra_variables)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data.enabled is not None:
        job.enabled = data.enabled
    if data.run_as_user_id is not None:
        try:
            job.run_as_user_id = resolve_run_as_user_id(actor=user, requested_run_as=data.run_as_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    if data.cron_expression is not None:
        job.cron_expression = data.cron_expression or None
        job.next_run_at = _next_run_or_none(job.cron_expression)
    await db.commit()
    await db.refresh(job)
    return _job_response(job)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> None:
    _require_automation_permission(user, "automation:write")
    job = await _load_job(db, user=user, job_id=job_id)
    await db.delete(job)
    await db.commit()


@router.post("/jobs/{job_id}/run", response_model=JobRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_job(
    job_id: str,
    data: JobRunRequest,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> JobRunResponse:
    _require_automation_permission(user, "automation:write")
    job = await _load_job(db, user=user, job_id=job_id)
    try:
        run = await enqueue_job_definition(
            db=db,
            queue=queue,
            definition=job,
            actor=user,
            extra_override=data.extra_variables,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _run_response(run)


@router.get("/jobs/{job_id}/runs", response_model=list[JobRunResponse])
async def list_job_runs(
    job_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[JobRunResponse]:
    _require_automation_permission(user, "automation:read")
    await _load_job(db, user=user, job_id=job_id)
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(AutomationJobRun)
        .where(
            AutomationJobRun.tenant_id == tenant_id,
            AutomationJobRun.job_definition_id == job_id,
        )
        .order_by(AutomationJobRun.created_at.desc())
        .limit(100)
    )
    return [_run_response(run) for run in result.scalars().all()]


@router.post("/adhoc", response_model=JobRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_adhoc(
    data: AdhocJobCreate,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> JobRunResponse:
    """立即下发临时/批量命令；``save=true`` 时同时留下作业定义。"""

    _require_automation_permission(user, "automation:write")
    job = _build_job(
        data=JobDefinitionCreate(
            name=data.name,
            job_type="batch.command" if len(data.target_asset_ids) > 1 else "adhoc.command",
            payload={"command": data.command, "target_asset_ids": data.target_asset_ids},
            extra_variables=data.extra_variables,
            run_as_user_id=data.run_as_user_id,
        ),
        user=user,
    )
    if data.save:
        db.add(job)
        await db.commit()
        await db.refresh(job)
    try:
        run = await enqueue_job_definition(db=db, queue=queue, definition=job, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _run_response(run)


@router.post("/cron/dispatch", response_model=CronDispatchResponse)
async def dispatch_cron(
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> CronDispatchResponse:
    _require_automation_permission(user, "automation:write")
    dispatched = await dispatch_due_cron_jobs(db=db, queue=queue, now=datetime.now(UTC))
    return CronDispatchResponse(dispatched_job_ids=dispatched)


def _build_job(*, data: JobDefinitionCreate, user: dict[str, Any]) -> JobDefinition:
    try:
        payload = validate_job_definition_payload(job_type=data.job_type, payload=data.payload)
        extra = validate_extra_variables(data.extra_variables)
        run_as = resolve_run_as_user_id(actor=user, requested_run_as=data.run_as_user_id)
        next_run = _next_run_or_none(data.cron_expression)
    except ValueError as exc:
        status_code = 403 if str(exc) == "JOB_RUNAS_FORBIDDEN" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return JobDefinition(
        id=new_job_id(),
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        job_type=data.job_type,
        payload=payload,
        extra_variables=extra,
        cron_expression=data.cron_expression or None,
        next_run_at=next_run,
        enabled=data.enabled,
        run_as_user_id=run_as,
        created_by=str(user.get("id") or ""),
    )


def _next_run_or_none(expression: str | None) -> datetime | None:
    if not expression:
        return None
    parse_cron_expression(expression)
    return next_cron_run(expression, after=datetime.now(UTC))


async def _load_job(db: AsyncSession, *, user: dict[str, Any], job_id: str) -> JobDefinition:
    job = await get_job_definition(
        db, tenant_id=str(user.get("tenant_id") or "default"), job_id=job_id
    )
    if job is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return job


def _job_response(job: JobDefinition) -> JobDefinitionResponse:
    return JobDefinitionResponse(
        id=job.id,
        name=job.name,
        job_type=job.job_type,
        payload=job.payload,
        extra_variables=job.extra_variables,
        cron_expression=job.cron_expression,
        next_run_at=job.next_run_at,
        enabled=job.enabled,
        run_as_user_id=job.run_as_user_id,
        created_by=job.created_by,
    )


def _run_response(run: AutomationJobRun) -> JobRunResponse:
    return JobRunResponse(
        message_id=run.message_id,
        job_type=run.job_type,
        status=run.status,
        requested_by=run.requested_by,
        playbook_name=run.playbook_name,
        job_definition_id=run.job_definition_id,
        extra_variables=run.extra_variables,
        run_as_user_id=run.run_as_user_id,
    )


def _require_automation_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
