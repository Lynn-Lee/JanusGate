"""SSH CA temporary certificate service primitives."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.ssh_ca import SshCertificate, SshCertificateAuthority


@dataclass(frozen=True)
class SshCertificateSigningRequest:
    tenant_id: str
    ca_id: int
    private_key_secret_id: str
    asset_id: int
    account_id: int
    principal: str
    public_key: str
    serial: str
    valid_after: datetime
    valid_before: datetime


class SshCertificateSigner(Protocol):
    async def sign(self, request: SshCertificateSigningRequest) -> str: ...


class SshCertificateService:
    def __init__(self, *, session: AsyncSession, signer: SshCertificateSigner) -> None:
        self._session = session
        self._signer = signer

    async def issue_certificate(
        self,
        *,
        tenant_id: str,
        ca_id: int,
        asset_id: int,
        account_id: int,
        principal: str,
        public_key: str,
        requested_by: str,
        now: datetime | None = None,
    ) -> SshCertificate:
        issued_at = _as_utc(now or datetime.now(UTC))
        ca = await self._get_active_ca(tenant_id=tenant_id, ca_id=ca_id)
        asset = await self._get_asset(tenant_id=tenant_id, asset_id=asset_id)
        account = await self._get_account(
            tenant_id=tenant_id, account_id=account_id, asset_id=asset_id
        )

        if asset.trusted_ssh_ca_id != ca.id:
            raise ValueError("ASSET_SSH_CA_NOT_TRUSTED")

        serial = f"{tenant_id}-{ca.id}-{asset.id}-{account.id}"
        valid_before = issued_at + timedelta(seconds=ca.validity_seconds)
        request = SshCertificateSigningRequest(
            tenant_id=tenant_id,
            ca_id=ca.id,
            private_key_secret_id=ca.private_key_secret_id,
            asset_id=asset.id,
            account_id=account.id,
            principal=principal,
            public_key=public_key,
            serial=serial,
            valid_after=issued_at,
            valid_before=valid_before,
        )
        certificate_body = await self._signer.sign(request)
        certificate = SshCertificate(
            tenant_id=tenant_id,
            ca_id=ca.id,
            asset_id=asset.id,
            account_id=account.id,
            principal=principal,
            public_key=public_key,
            serial=serial,
            status="issued",
            certificate_body=certificate_body,
            requested_by=requested_by,
            valid_after=issued_at,
            valid_before=valid_before,
        )
        self._session.add(certificate)
        await self._session.commit()
        return certificate

    async def revoke_certificate(
        self,
        *,
        tenant_id: str,
        certificate_id: int,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        result = await self._session.execute(
            select(SshCertificate)
            .where(SshCertificate.id == certificate_id)
            .where(SshCertificate.tenant_id == tenant_id)
        )
        certificate = result.scalar_one_or_none()
        if certificate is None or certificate.status != "issued":
            return False

        certificate.status = "revoked"
        certificate.revoked_at = _as_utc(now or datetime.now(UTC))
        certificate.revoke_reason = reason
        await self._session.commit()
        return True

    async def _get_active_ca(self, *, tenant_id: str, ca_id: int) -> SshCertificateAuthority:
        result = await self._session.execute(
            select(SshCertificateAuthority)
            .where(SshCertificateAuthority.id == ca_id)
            .where(SshCertificateAuthority.tenant_id == tenant_id)
            .where(SshCertificateAuthority.status == "active")
        )
        ca = result.scalar_one_or_none()
        if ca is None:
            raise ValueError("SSH_CA_NOT_FOUND")
        return ca

    async def _get_asset(self, *, tenant_id: str, asset_id: int) -> Asset:
        result = await self._session.execute(
            select(Asset)
            .where(Asset.id == asset_id)
            .where(Asset.tenant_id == tenant_id)
            .where(Asset.is_active.is_(True))
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            raise ValueError("ASSET_NOT_FOUND")
        return asset

    async def _get_account(self, *, tenant_id: str, account_id: int, asset_id: int) -> Account:
        result = await self._session.execute(
            select(Account)
            .where(Account.id == account_id)
            .where(Account.tenant_id == tenant_id)
            .where(Account.asset_id == asset_id)
            .where(Account.protocol == "ssh")
            .where(Account.status == "active")
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise ValueError("ACCOUNT_NOT_FOUND")
        return account


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
