"""Phase 4 automation job scheduling API routes."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.deps import current_user, get_redis
from app.services.automation_worker import AutomationJobQueue, RedisStreamClient

router = APIRouter(prefix="/automation/jobs", tags=["Automation Jobs"])


class AssetScanJobCreate(BaseModel):
    asset_id: int = Field(gt=0)
    scan_profile: str = Field(min_length=1, max_length=128)


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


def _require_automation_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
