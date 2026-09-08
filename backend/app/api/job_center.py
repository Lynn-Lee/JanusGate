"""#t77 作业中心：Playbook 目录 / Job 定义 / 执行记录（复用 #t52 队列）。"""
from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.automation import get_automation_job_queue
from app.api.job_center_schemas import (
    JobCreate,
    JobExecutionListResponse,
    JobExecutionResponse,
    JobListResponse,
    JobPlaybookCreate,
    JobPlaybookListResponse,
    JobPlaybookResponse,
    JobPlaybookUpdate,
    JobResponse,
    JobRunRequest,
    JobRunResponse,
    JobUpdate,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.asset import Asset
from app.models.automation import Job, JobExecution, JobPlaybook
from app.services.automation_worker import AutomationJobQueue, JsonValue
from app.services.job_center import (
    decode_target_asset_ids,
    encode_target_asset_ids,
    resolve_playbook_file,
    validate_playbook_relative_name,
)
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(prefix="/job-center", tags=["作业中心"])


def _require_automation_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _require_list_permission(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    allowed = {"admin", "automation:read", "automation:write"}
    if allowed.intersection(permissions):
        return
    raise HTTPException(status_code=403, detail="缺少权限: automation:read")


@router.get("/playbooks/", response_model=JobPlaybookListResponse)
async def list_playbooks(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobPlaybookListResponse:
    _require_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(JobPlaybook, actor_scope).order_by(JobPlaybook.id.asc())
    )
    rows = list(result.scalars().all())
    return JobPlaybookListResponse(
        items=[_playbook_response(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "/playbooks/",
    response_model=JobPlaybookResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_playbook(
    data: JobPlaybookCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobPlaybookResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    playbook_name = _validated_playbook_name(data.playbook_name)
    row = JobPlaybook(
        tenant_id=actor_scope.tenant_id,
        name=name,
        playbook_name=playbook_name,
        description=data.description.strip(),
        is_active=data.is_active,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="PLAYBOOK_NAME_CONFLICT") from exc
    await db.refresh(row)
    return _playbook_response(row)


@router.patch("/playbooks/{playbook_id}", response_model=JobPlaybookResponse)
async def update_playbook(
    playbook_id: int,
    data: JobPlaybookUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobPlaybookResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    row = await _get_scoped_playbook(db, actor_scope, playbook_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        row.name = name
    if "playbook_name" in payload:
        row.playbook_name = _validated_playbook_name(str(payload["playbook_name"] or ""))
    if "description" in payload:
        row.description = str(payload["description"] or "").strip()
    if "is_active" in payload and payload["is_active"] is not None:
        row.is_active = bool(payload["is_active"])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="PLAYBOOK_NAME_CONFLICT") from exc
    await db.refresh(row)
    return _playbook_response(row)


@router.delete(
    "/playbooks/{playbook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_playbook(
    playbook_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    row = await _get_scoped_playbook(db, actor_scope, playbook_id)
    job_count = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.tenant_id == actor_scope.tenant_id)
        .where(Job.playbook_id == row.id)
    )
    if int(job_count or 0) > 0:
        raise HTTPException(status_code=409, detail="PLAYBOOK_IN_USE")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/jobs/", response_model=JobListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobListResponse:
    _require_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(Job, actor_scope).order_by(Job.id.asc()))
    rows = list(result.scalars().all())
    playbook_names = await _playbook_name_map(
        db, actor_scope.tenant_id, {row.playbook_id for row in rows}
    )
    return JobListResponse(
        items=[_job_response(row, playbook_names.get(row.playbook_id)) for row in rows],
        total=len(rows),
    )


@router.post("/jobs/", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    data: JobCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    playbook = await _get_scoped_playbook(db, actor_scope, data.playbook_id)
    if not playbook.is_active:
        raise HTTPException(status_code=400, detail="PLAYBOOK_INACTIVE")
    await _ensure_active_assets(db, actor_scope.tenant_id, list(data.target_asset_ids))
    row = Job(
        tenant_id=actor_scope.tenant_id,
        name=name,
        playbook_id=playbook.id,
        target_asset_ids_json=encode_target_asset_ids(list(data.target_asset_ids)),
        check_mode=data.check_mode,
        description=data.description.strip(),
        is_active=data.is_active,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="JOB_NAME_CONFLICT") from exc
    await db.refresh(row)
    return _job_response(row, playbook.playbook_name)


@router.patch("/jobs/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: int,
    data: JobUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobResponse:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    row = await _get_scoped_job(db, actor_scope, job_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        row.name = name
    playbook_name: str | None = None
    if "playbook_id" in payload and payload["playbook_id"] is not None:
        playbook = await _get_scoped_playbook(db, actor_scope, int(payload["playbook_id"]))
        if not playbook.is_active:
            raise HTTPException(status_code=400, detail="PLAYBOOK_INACTIVE")
        row.playbook_id = playbook.id
        playbook_name = playbook.playbook_name
    if "target_asset_ids" in payload and payload["target_asset_ids"] is not None:
        target_ids = list(payload["target_asset_ids"])
        await _ensure_active_assets(db, actor_scope.tenant_id, target_ids)
        row.target_asset_ids_json = encode_target_asset_ids(target_ids)
    if "check_mode" in payload and payload["check_mode"] is not None:
        row.check_mode = bool(payload["check_mode"])
    if "description" in payload:
        row.description = str(payload["description"] or "").strip()
    if "is_active" in payload and payload["is_active"] is not None:
        row.is_active = bool(payload["is_active"])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="JOB_NAME_CONFLICT") from exc
    await db.refresh(row)
    if playbook_name is None:
        playbook_names = await _playbook_name_map(
            db, actor_scope.tenant_id, {row.playbook_id}
        )
        playbook_name = playbook_names.get(row.playbook_id)
    return _job_response(row, playbook_name)


@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    row = await _get_scoped_job(db, actor_scope, job_id)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/jobs/{job_id}/run",
    response_model=JobRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_job(
    job_id: int,
    data: JobRunRequest | None = None,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> JobRunResponse:
    """Enqueue ansible.playbook via #t52 JSON-only queue; create JobExecution."""

    _require_automation_permission(user, "automation:write")
    actor_scope = actor_scope_from_user(user)
    job = await _get_scoped_job(db, actor_scope, job_id)
    if not job.is_active:
        raise HTTPException(status_code=400, detail="JOB_INACTIVE")
    playbook = await _get_scoped_playbook(db, actor_scope, job.playbook_id)
    if not playbook.is_active:
        raise HTTPException(status_code=400, detail="PLAYBOOK_INACTIVE")
    # Re-validate catalog path against root at run time (fail-closed).
    try:
        resolve_playbook_file(playbook.playbook_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc.args[0])) from exc

    overrides = data or JobRunRequest()
    if overrides.target_asset_ids is not None:
        target_asset_ids = list(overrides.target_asset_ids)
    else:
        target_asset_ids = decode_target_asset_ids(job.target_asset_ids_json)
    if not target_asset_ids:
        raise HTTPException(status_code=400, detail="JOB_TARGETS_REQUIRED")
    await _ensure_active_assets(db, actor_scope.tenant_id, target_asset_ids)
    check_mode = (
        bool(overrides.check_mode) if overrides.check_mode is not None else job.check_mode
    )

    execution = JobExecution(
        tenant_id=actor_scope.tenant_id,
        job_id=job.id,
        message_id=f"pending-{uuid4()}",
        status="queued",
        requested_by=actor_scope.user_id,
        playbook_name=playbook.playbook_name,
        check_mode=check_mode,
        target_count=len(target_asset_ids),
        error_code=None,
    )
    db.add(execution)
    await db.flush()

    target_values = cast(list[JsonValue], [int(asset_id) for asset_id in target_asset_ids])
    payload: dict[str, JsonValue] = {
        "playbook_name": playbook.playbook_name,
        "target_asset_ids": target_values,
        "check_mode": check_mode,
        "job_execution_id": execution.id,
        "job_id": job.id,
    }
    try:
        message_id = await queue.enqueue(
            tenant_id=actor_scope.tenant_id,
            requested_by=actor_scope.user_id,
            job_type="ansible.playbook",
            payload=payload,
        )
    except Exception:
        await db.rollback()
        raise
    execution.message_id = message_id
    await db.commit()
    await db.refresh(execution)
    return JobRunResponse(
        execution_id=execution.id,
        job_id=job.id,
        message_id=message_id,
        status=execution.status,
    )


@router.get("/executions/", response_model=JobExecutionListResponse)
async def list_executions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> JobExecutionListResponse:
    _require_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    total = await db.scalar(
        select(func.count())
        .select_from(JobExecution)
        .where(JobExecution.tenant_id == actor_scope.tenant_id)
    )
    result = await db.execute(
        select(JobExecution)
        .where(JobExecution.tenant_id == actor_scope.tenant_id)
        .order_by(JobExecution.created_at.desc(), JobExecution.id.desc())
        .limit(100)
    )
    rows = list(result.scalars().all())
    return JobExecutionListResponse(
        items=[_execution_response(row) for row in rows],
        total=int(total or 0),
    )


def _validated_playbook_name(playbook_name: str) -> str:
    try:
        safe_name = validate_playbook_relative_name(playbook_name)
        resolve_playbook_file(safe_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc.args[0])) from exc
    return safe_name


async def _ensure_active_assets(
    db: AsyncSession, tenant_id: str, asset_ids: list[int]
) -> None:
    result = await db.execute(
        select(Asset.id)
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
        .where(Asset.id.in_(asset_ids))
    )
    found = {int(row[0]) for row in result.all()}
    if any(asset_id not in found for asset_id in asset_ids):
        raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")


async def _get_scoped_playbook(
    db: AsyncSession, actor_scope: ActorScope, playbook_id: int
) -> JobPlaybook:
    result = await db.execute(
        scoped_select(JobPlaybook, actor_scope).where(JobPlaybook.id == playbook_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="PLAYBOOK_NOT_FOUND")
    return row


async def _get_scoped_job(db: AsyncSession, actor_scope: ActorScope, job_id: int) -> Job:
    result = await db.execute(scoped_select(Job, actor_scope).where(Job.id == job_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return row


async def _playbook_name_map(
    db: AsyncSession, tenant_id: str, playbook_ids: set[int]
) -> dict[int, str]:
    if not playbook_ids:
        return {}
    result = await db.execute(
        select(JobPlaybook.id, JobPlaybook.playbook_name)
        .where(JobPlaybook.tenant_id == tenant_id)
        .where(JobPlaybook.id.in_(playbook_ids))
    )
    return {int(row[0]): str(row[1]) for row in result.all()}


def _playbook_response(row: JobPlaybook) -> JobPlaybookResponse:
    return JobPlaybookResponse(
        id=row.id,
        name=row.name,
        playbook_name=row.playbook_name,
        description=row.description,
        is_active=row.is_active,
    )


def _job_response(row: Job, playbook_name: str | None) -> JobResponse:
    return JobResponse(
        id=row.id,
        name=row.name,
        playbook_id=row.playbook_id,
        playbook_name=playbook_name,
        target_asset_ids=decode_target_asset_ids(row.target_asset_ids_json),
        check_mode=row.check_mode,
        description=row.description,
        is_active=row.is_active,
    )


def _execution_response(row: JobExecution) -> JobExecutionResponse:
    return JobExecutionResponse(
        id=row.id,
        job_id=row.job_id,
        message_id=row.message_id,
        status=row.status,
        requested_by=row.requested_by,
        playbook_name=row.playbook_name,
        check_mode=row.check_mode,
        target_count=row.target_count,
        error_code=row.error_code,
    )
