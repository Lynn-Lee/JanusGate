"""Phase 4 SSH CA management API routes."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ssh_certificate_schemas import (
    SshCertificateAuthorityCreateRequest,
    SshCertificateAuthorityListResponse,
    SshCertificateAuthorityResponse,
    SshCertificateAuthorityTrustBundleItem,
    SshCertificateAuthorityTrustBundleResponse,
)
from app.core.database import get_db
from app.core.deps import current_user
from app.models.asset import Asset
from app.models.ssh_ca import SshCertificateAuthority

router = APIRouter(prefix="/ssh-certificate-authorities", tags=["SSH CA"])


@router.get("/", response_model=SshCertificateAuthorityListResponse)
async def list_ssh_certificate_authorities(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SshCertificateAuthorityListResponse:
    _require_ssh_ca_permission(user, "ssh-certificate-authorities:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SshCertificateAuthority)
        .where(SshCertificateAuthority.tenant_id == tenant_id)
        .order_by(SshCertificateAuthority.id)
    )
    authorities = result.scalars().all()
    items = [_authority_response(authority) for authority in authorities]
    return SshCertificateAuthorityListResponse(items=items, total=len(items))


@router.get("/trust-bundle", response_model=SshCertificateAuthorityTrustBundleResponse)
async def get_ssh_ca_trust_bundle(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SshCertificateAuthorityTrustBundleResponse:
    _require_ssh_ca_permission(user, "ssh-certificate-authorities:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SshCertificateAuthority, Asset.id)
        .join(Asset, Asset.trusted_ssh_ca_id == SshCertificateAuthority.id)
        .where(SshCertificateAuthority.tenant_id == tenant_id)
        .where(SshCertificateAuthority.status == "active")
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
        .order_by(SshCertificateAuthority.id, Asset.id)
    )

    bundle_by_ca_id: dict[int, SshCertificateAuthorityTrustBundleItem] = {}
    for authority, asset_id in result.all():
        item = bundle_by_ca_id.get(authority.id)
        if item is None:
            item = SshCertificateAuthorityTrustBundleItem(
                ca_id=authority.id,
                tenant_id=authority.tenant_id,
                name=authority.name,
                public_key=authority.public_key,
                trusted_asset_ids=[],
            )
            bundle_by_ca_id[authority.id] = item
        item.trusted_asset_ids.append(asset_id)

    items = list(bundle_by_ca_id.values())
    return SshCertificateAuthorityTrustBundleResponse(items=items, total=len(items))


@router.post(
    "/",
    response_model=SshCertificateAuthorityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ssh_certificate_authority(
    data: SshCertificateAuthorityCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SshCertificateAuthorityResponse:
    _require_ssh_ca_permission(user, "ssh-certificate-authorities:create")
    tenant_id = str(user.get("tenant_id") or "default")
    existing = await db.execute(
        select(SshCertificateAuthority)
        .where(SshCertificateAuthority.tenant_id == tenant_id)
        .where(SshCertificateAuthority.name == data.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="SSH_CA_ALREADY_EXISTS")

    authority = SshCertificateAuthority(
        tenant_id=tenant_id,
        name=data.name,
        public_key=data.public_key,
        private_key_secret_id=data.private_key_secret_id,
        status="active",
        validity_seconds=data.validity_seconds,
    )
    db.add(authority)
    await db.commit()
    await db.refresh(authority)
    return _authority_response(authority)


@router.post("/{authority_id}/disable", response_model=SshCertificateAuthorityResponse)
async def disable_ssh_certificate_authority(
    authority_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SshCertificateAuthorityResponse:
    _require_ssh_ca_permission(user, "ssh-certificate-authorities:disable")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SshCertificateAuthority)
        .where(SshCertificateAuthority.id == authority_id)
        .where(SshCertificateAuthority.tenant_id == tenant_id)
        .where(SshCertificateAuthority.status == "active")
    )
    authority = result.scalar_one_or_none()
    if authority is None:
        raise HTTPException(status_code=404, detail="SSH_CA_NOT_FOUND")

    authority.status = "disabled"
    await db.commit()
    await db.refresh(authority)
    return _authority_response(authority)


def _require_ssh_ca_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _authority_response(authority: SshCertificateAuthority) -> SshCertificateAuthorityResponse:
    return SshCertificateAuthorityResponse(
        id=authority.id,
        tenant_id=authority.tenant_id,
        name=authority.name,
        public_key=authority.public_key,
        status=authority.status,
        validity_seconds=authority.validity_seconds,
    )
