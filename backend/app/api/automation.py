"""Phase 4 automation job scheduling API routes."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import current_user, get_redis
from app.models.account import Account
from app.services.automation_worker import AutomationJobQueue, RedisStreamClient
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/automation/jobs", tags=["Automation Jobs"])


class AssetScanJobCreate(BaseModel):
    asset_id: int = Field(gt=0)
    scan_profile: str = Field(min_length=1, max_length=128)


class CredentialRotationJobCreate(BaseModel):
    account_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=240)


class AutomationJobResponse(BaseModel):
    job_id: str
    job_type: str
    status: str


def get_automation_job_queue(
    redis: RedisStreamClient = Depends(get_redis),
) -> AutomationJobQueue:
    return AutomationJobQueue(redis=redis)


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
