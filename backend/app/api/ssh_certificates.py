"""Phase 4 SSH CA temporary certificate API routes."""
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ssh_certificate_schemas import (
    SshCertificateIssueRequest,
    SshCertificateListResponse,
    SshCertificateResponse,
    SshCertificateRevokeRequest,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import current_user
from app.models.account import Account
from app.models.ssh_ca import SshCertificate
from app.services.ssh_ca import (
    SshCaSecretProvider,
    SshCertificateService,
    SshCertificateSigner,
    VaultOpenSshCertificateSigner,
)
from app.tenancy.scope import actor_scope_from_user, scoped_select
from app.vault.provider import LocalEncryptedSecretProvider

router = APIRouter(prefix="/ssh-certificates", tags=["SSH CA"])


@lru_cache
def _default_ssh_ca_secret_provider() -> LocalEncryptedSecretProvider:
    master_key = sha256(settings.SECRET_KEY.encode()).digest()
    return LocalEncryptedSecretProvider(master_key=master_key)


def get_ssh_ca_secret_provider() -> SshCaSecretProvider:
    return _default_ssh_ca_secret_provider()


def get_ssh_certificate_signer(
    secret_provider: Annotated[SshCaSecretProvider, Depends(get_ssh_ca_secret_provider)],
) -> SshCertificateSigner:
    return VaultOpenSshCertificateSigner(secret_provider=secret_provider)


@router.get("/", response_model=SshCertificateListResponse)
async def list_ssh_certificates(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SshCertificateListResponse:
    _require_ssh_certificate_permission(user, "ssh-certificates:read")
    scoped_accounts = scoped_select(Account, actor_scope_from_user(user)).subquery()
    result = await db.execute(
        select(SshCertificate)
        .join(scoped_accounts, scoped_accounts.c.id == SshCertificate.account_id)
        .order_by(SshCertificate.id)
    )
    certificates = result.scalars().all()
    items = [_certificate_response(certificate) for certificate in certificates]
    return SshCertificateListResponse(items=items, total=len(items))


@router.post(
    "/",
    response_model=SshCertificateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_ssh_certificate(
    data: SshCertificateIssueRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
    signer: SshCertificateSigner = Depends(get_ssh_certificate_signer),
) -> SshCertificateResponse:
    _require_ssh_certificate_permission(user, "ssh-certificates:issue")
    tenant_id = str(user.get("tenant_id") or "default")
    await _assert_account_visible(
        db=db,
        user=user,
        account_id=data.account_id,
        asset_id=data.asset_id,
    )

    service = SshCertificateService(session=db, signer=signer)
    try:
        certificate = await service.issue_certificate(
            tenant_id=tenant_id,
            ca_id=data.ca_id,
            asset_id=data.asset_id,
            account_id=data.account_id,
            principal=data.principal,
            public_key=data.public_key,
            requested_by=str(user.get("id") or ""),
        )
    except ValueError as exc:
        _raise_service_error(str(exc))
    return _certificate_response(certificate)


@router.post("/{certificate_id}/revoke", response_model=SshCertificateResponse)
async def revoke_ssh_certificate(
    certificate_id: int,
    data: SshCertificateRevokeRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
    signer: SshCertificateSigner = Depends(get_ssh_certificate_signer),
) -> SshCertificateResponse:
    _require_ssh_certificate_permission(user, "ssh-certificates:revoke")
    tenant_id = str(user.get("tenant_id") or "default")
    certificate = await _get_visible_certificate(db=db, user=user, certificate_id=certificate_id)

    service = SshCertificateService(session=db, signer=signer)
    revoked = await service.revoke_certificate(
        tenant_id=tenant_id,
        certificate_id=certificate.id,
        reason=data.reason,
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="SSH_CERTIFICATE_NOT_FOUND")

    await db.refresh(certificate)
    return _certificate_response(certificate)


def _require_ssh_certificate_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _assert_account_visible(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    account_id: int,
    asset_id: int,
) -> None:
    result = await db.execute(
        scoped_select(Account, actor_scope_from_user(user))
        .where(Account.id == account_id)
        .where(Account.asset_id == asset_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="ACCOUNT_NOT_FOUND")


async def _get_visible_certificate(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    certificate_id: int,
) -> SshCertificate:
    scoped_accounts = scoped_select(Account, actor_scope_from_user(user)).subquery()
    result = await db.execute(
        select(SshCertificate)
        .join(scoped_accounts, scoped_accounts.c.id == SshCertificate.account_id)
        .where(SshCertificate.id == certificate_id)
        .where(SshCertificate.tenant_id == str(user.get("tenant_id") or "default"))
    )
    certificate = result.scalar_one_or_none()
    if certificate is None:
        raise HTTPException(status_code=404, detail="SSH_CERTIFICATE_NOT_FOUND")
    return certificate


def _raise_service_error(code: str) -> None:
    if code in {"SSH_CA_NOT_FOUND", "ASSET_NOT_FOUND", "ACCOUNT_NOT_FOUND"}:
        raise HTTPException(status_code=404, detail=code)
    if code == "ASSET_SSH_CA_NOT_TRUSTED":
        raise HTTPException(status_code=403, detail=code)
    raise HTTPException(status_code=400, detail=code)


def _certificate_response(certificate: SshCertificate) -> SshCertificateResponse:
    return SshCertificateResponse(
        id=certificate.id,
        tenant_id=certificate.tenant_id,
        ca_id=certificate.ca_id,
        asset_id=certificate.asset_id,
        account_id=certificate.account_id,
        principal=certificate.principal,
        public_key=certificate.public_key,
        serial=certificate.serial,
        status=certificate.status,
        certificate_body=certificate.certificate_body,
        requested_by=certificate.requested_by,
        valid_after=_as_utc(certificate.valid_after),
        valid_before=_as_utc(certificate.valid_before),
        revoked_at=_as_optional_utc(certificate.revoked_at),
        revoke_reason=certificate.revoke_reason,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=UTC)


def _as_optional_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _as_utc(value)
