"""Phase 4 automation job scheduling API routes."""
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.models.account import Account
from app.models.automation import AutomationJobRun
from app.services.automation_worker import AutomationJobQueue, JsonValue, RedisStreamClient
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/automation/jobs", tags=["Automation Jobs"])


class AssetScanJobCreate(BaseModel):
    asset_id: int = Field(gt=0)
    scan_profile: str = Field(min_length=1, max_length=128)


class CredentialRotationJobCreate(BaseModel):
    account_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=240)


class PlaybookJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playbook_name: str = Field(min_length=1, max_length=128)
    target_asset_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=200)
    check_mode: bool = False


class AutomationJobResponse(BaseModel):
    job_id: str
    job_type: str
    status: str


class AutomationJobRunResponse(BaseModel):
    message_id: str
    job_type: str
    status: str
    requested_by: str
    playbook_name: str | None
    check_mode: bool | None
    target_count: int | None
    error_code: str | None


class AutomationJobRunListResponse(BaseModel):
    items: list[AutomationJobRunResponse]
    total: int


def get_automation_job_queue(
    redis: RedisStreamClient = Depends(get_redis),
) -> AutomationJobQueue:
    return AutomationJobQueue(redis=redis)


@router.get("/runs", response_model=AutomationJobRunListResponse)
async def list_automation_job_runs(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> AutomationJobRunListResponse:
    _require_automation_permission(user, "automation:read")
    tenant_id = str(user.get("tenant_id") or "default")
    total_result = await db.execute(
        select(func.count()).select_from(AutomationJobRun).where(
            AutomationJobRun.tenant_id == tenant_id
        )
    )
    result = await db.execute(
        select(AutomationJobRun)
        .where(AutomationJobRun.tenant_id == tenant_id)
        .order_by(AutomationJobRun.created_at.desc(), AutomationJobRun.message_id.desc())
        .limit(100)
    )
    runs = result.scalars().all()
    return AutomationJobRunListResponse(
        items=[
            AutomationJobRunResponse(
                message_id=run.message_id,
                job_type=run.job_type,
                status=run.status,
                requested_by=run.requested_by,
                playbook_name=run.playbook_name,
                check_mode=run.check_mode,
                target_count=run.target_count,
                error_code=run.error_code,
            )
            for run in runs
        ],
        total=total_result.scalar_one(),
    )


@router.post(
    "/asset-scans",
    response_model=AutomationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_asset_scan_job(
    data: AssetScanJobCreate,
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> AutomationJobResponse:
    _require_automation_permission(user, "automation:write")
    job_id = await queue.enqueue(
        tenant_id=str(user.get("tenant_id") or "default"),
        requested_by=str(user["id"]),
        job_type="asset.scan",
        payload={
            "asset_id": data.asset_id,
            "scan_profile": data.scan_profile,
        },
    )
    return AutomationJobResponse(job_id=job_id, job_type="asset.scan", status="queued")


@router.post(
    "/credential-rotations",
    response_model=AutomationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_credential_rotation_job(
    data: CredentialRotationJobCreate,
    db: AsyncSession = Depends(get_db),
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> AutomationJobResponse:
    _require_automation_permission(user, "automation:write")
    account = await _get_scoped_account(db=db, user=user, account_id=data.account_id)
    payload: dict[str, int | str] = {"account_id": account.id}
    if data.reason is not None:
        payload["reason"] = data.reason
    job_id = await queue.enqueue(
        tenant_id=account.tenant_id,
        requested_by=str(user["id"]),
        job_type="credential.rotate",
        payload=payload,
    )
    return AutomationJobResponse(job_id=job_id, job_type="credential.rotate", status="queued")


@router.post(
    "/playbooks",
    response_model=AutomationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_playbook_job(
    data: PlaybookJobCreate,
    queue: AutomationJobQueue = Depends(get_automation_job_queue),
    user: dict[str, Any] = Depends(current_user),
) -> AutomationJobResponse:
    _require_automation_permission(user, "automation:write")
    target_asset_ids = cast(list[JsonValue], list(data.target_asset_ids))
    payload: dict[str, JsonValue] = {
        "playbook_name": data.playbook_name,
        "target_asset_ids": target_asset_ids,
        "check_mode": data.check_mode,
    }
    job_id = await queue.enqueue(
        tenant_id=str(user.get("tenant_id") or "default"),
        requested_by=str(user["id"]),
        job_type="ansible.playbook",
        payload=payload,
    )
    return AutomationJobResponse(job_id=job_id, job_type="ansible.playbook", status="queued")


def _require_automation_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _get_scoped_account(
    *, db: AsyncSession, user: dict[str, Any], account_id: int
) -> Account:
    result = await db.execute(
        scoped_select(Account, actor_scope_from_user(user)).where(Account.id == account_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="ACCOUNT_NOT_FOUND")
    return account
