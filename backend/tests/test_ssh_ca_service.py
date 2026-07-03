"""Phase 4 SSH CA service tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.ssh_ca import SshCertificate, SshCertificateAuthority
from app.services.ssh_ca import (
    SshCertificateService,
    SshCertificateSigner,
    SshCertificateSigningRequest,
)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class RecordingSigner(SshCertificateSigner):
    def __init__(self) -> None:
        self.requests: list[SshCertificateSigningRequest] = []

    async def sign(self, request: SshCertificateSigningRequest) -> str:
        self.requests.append(request)
        return f"ssh-rsa-cert-v01@openssh.com {request.serial} {request.principal}"


async def seed_ca_asset_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trusted_ca_id: int | None = 1,
    asset_tenant_id: str = "tenant-a",
) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            SshCertificateAuthority(
                id=1,
                tenant_id="tenant-a",
                name="tenant-a-prod-ca",
                public_key="ssh-rsa AAAAca",
                private_key_secret_id="sec_tenant_a_ssh_ca",
                status="active",
                validity_seconds=900,
            )
        )
        session.add(
            Asset(
                id=1,
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                trusted_ssh_ca_id=trusted_ca_id,
                tenant_id=asset_tenant_id,
            )
        )
        session.add(
            Account(
                id=1,
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_ssh_ca_service_issues_temporary_certificate_for_trusted_asset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ca_asset_account(session_factory)
    signer = RecordingSigner()

    async with session_factory() as session:
        service = SshCertificateService(session=session, signer=signer)
        certificate = await service.issue_certificate(
            tenant_id="tenant-a",
            ca_id=1,
            asset_id=1,
            account_id=1,
            principal="deploy",
            public_key="ssh-rsa AAAAuser",
            requested_by="user-1",
            now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
        )

        persisted = await session.get(SshCertificate, certificate.id)

    assert persisted is certificate
    assert certificate.tenant_id == "tenant-a"
    assert certificate.status == "issued"
    assert certificate.serial == "tenant-a-1-1-1"
    assert certificate.valid_after == datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    assert certificate.valid_before == datetime(2026, 7, 3, 12, 15, tzinfo=UTC)
    assert certificate.certificate_body == "ssh-rsa-cert-v01@openssh.com tenant-a-1-1-1 deploy"
    assert signer.requests[0].private_key_secret_id == "sec_tenant_a_ssh_ca"
    assert signer.requests[0].valid_before == certificate.valid_before


@pytest.mark.asyncio
async def test_ssh_ca_service_rejects_untrusted_or_cross_tenant_asset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ca_asset_account(session_factory, trusted_ca_id=None)

    async with session_factory() as session:
        service = SshCertificateService(session=session, signer=RecordingSigner())
        with pytest.raises(ValueError, match="ASSET_SSH_CA_NOT_TRUSTED"):
            await service.issue_certificate(
                tenant_id="tenant-a",
                ca_id=1,
                asset_id=1,
                account_id=1,
                principal="deploy",
                public_key="ssh-rsa AAAAuser",
                requested_by="user-1",
            )

    async with session_factory() as session:
        asset = await session.get(Asset, 1)
        assert asset is not None
        asset.tenant_id = "tenant-b"
        asset.trusted_ssh_ca_id = 1
        await session.commit()

    async with session_factory() as session:
        service = SshCertificateService(session=session, signer=RecordingSigner())
        with pytest.raises(ValueError, match="ASSET_NOT_FOUND"):
            await service.issue_certificate(
                tenant_id="tenant-a",
                ca_id=1,
                asset_id=1,
                account_id=1,
                principal="deploy",
                public_key="ssh-rsa AAAAuser",
                requested_by="user-1",
            )


@pytest.mark.asyncio
async def test_ssh_ca_service_revokes_issued_certificate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ca_asset_account(session_factory)

    async with session_factory() as session:
        service = SshCertificateService(session=session, signer=RecordingSigner())
        certificate = await service.issue_certificate(
            tenant_id="tenant-a",
            ca_id=1,
            asset_id=1,
            account_id=1,
            principal="deploy",
            public_key="ssh-rsa AAAAuser",
            requested_by="user-1",
            now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
        )

        revoked = await service.revoke_certificate(
            tenant_id="tenant-a",
            certificate_id=certificate.id,
            reason="operator-request",
            now=datetime(2026, 7, 3, 12, 5, tzinfo=UTC),
        )
        revoked_again = await service.revoke_certificate(
            tenant_id="tenant-a",
            certificate_id=certificate.id,
            reason="duplicate",
        )
        cross_tenant = await service.revoke_certificate(
            tenant_id="tenant-b",
            certificate_id=certificate.id,
            reason="wrong-tenant",
        )

        persisted = await session.get(SshCertificate, certificate.id)

    assert revoked is True
    assert revoked_again is False
    assert cross_tenant is False
    assert persisted is not None
    assert persisted.status == "revoked"
    assert persisted.revoke_reason == "operator-request"
    assert persisted.revoked_at == datetime(2026, 7, 3, 12, 5, tzinfo=UTC)
