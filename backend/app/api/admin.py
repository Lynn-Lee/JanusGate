"""Admin API routes for edition and license boundaries."""
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.deps import current_user
from app.core.license import LicenseSummary, get_license_summary

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/license-summary", response_model=LicenseSummary)
async def get_admin_license_summary(
    user: dict[str, Any] = Depends(current_user),
) -> LicenseSummary:
    if "admin" not in user.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少权限: admin")
    return get_license_summary(
        configured_edition=settings.JANUSGATE_EDITION,
        license_key=settings.JANUSGATE_LICENSE_KEY,
        signing_secret=settings.JANUSGATE_LICENSE_SIGNING_SECRET,
        now=datetime.now(UTC),
    )
