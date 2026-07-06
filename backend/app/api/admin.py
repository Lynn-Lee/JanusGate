"""Admin API routes for edition and license boundaries."""
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.core.license import Edition, LicenseSummary, LicenseVerifier, get_license_summary
from app.models.admin import LicenseConfigurationModel

router = APIRouter(prefix="/admin", tags=["Admin"])
ACTIVE_LICENSE_CONFIG_ID = "active"


class LicenseConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured_edition: Edition
    license_verifier: LicenseVerifier = "hmac"
    license_key: str
    license_signing_secret: str = ""
    license_public_key: str = ""


@router.get("/license-summary", response_model=LicenseSummary)
async def get_admin_license_summary(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> LicenseSummary:
    _require_admin(user)
    config = await db.get(LicenseConfigurationModel, ACTIVE_LICENSE_CONFIG_ID)
    if config is not None:
        return _build_summary_from_config(config)
    return get_license_summary(
        configured_edition=settings.JANUSGATE_EDITION,
        license_key=settings.JANUSGATE_LICENSE_KEY,
        signing_secret=settings.JANUSGATE_LICENSE_SIGNING_SECRET,
        public_key=settings.JANUSGATE_LICENSE_PUBLIC_KEY,
        license_verifier=settings.JANUSGATE_LICENSE_VERIFIER,
        now=datetime.now(UTC),
    )


@router.post("/license-config", response_model=LicenseSummary)
async def update_admin_license_config(
    payload: LicenseConfigRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> LicenseSummary:
    _require_admin(user)
    config = await db.get(LicenseConfigurationModel, ACTIVE_LICENSE_CONFIG_ID)
    if config is None:
        config = LicenseConfigurationModel(
            id=ACTIVE_LICENSE_CONFIG_ID,
            configured_edition=payload.configured_edition,
            license_verifier=payload.license_verifier,
            license_key=payload.license_key,
            license_signing_secret=payload.license_signing_secret,
            license_public_key=payload.license_public_key,
            updated_by=str(user.get("id", "")),
        )
        db.add(config)
    else:
        config.configured_edition = payload.configured_edition
        config.license_verifier = payload.license_verifier
        config.license_key = payload.license_key
        config.license_signing_secret = payload.license_signing_secret
        config.license_public_key = payload.license_public_key
        config.updated_by = str(user.get("id", ""))
    await db.flush()
    return _build_summary_from_config(config)


def _require_admin(user: dict[str, Any]) -> None:
    if "admin" not in user.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少权限: admin")


def _build_summary_from_config(config: LicenseConfigurationModel) -> LicenseSummary:
    return get_license_summary(
        configured_edition=cast(Edition, config.configured_edition),
        license_key=config.license_key,
        signing_secret=config.license_signing_secret,
        public_key=config.license_public_key,
        license_verifier=cast(LicenseVerifier, config.license_verifier),
        now=datetime.now(UTC),
    )
