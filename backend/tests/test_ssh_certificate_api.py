"""Phase 4 SSH CA temporary certificate API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_ssh_public_identity,
)
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.ssh_certificates import get_ssh_ca_secret_provider
from app.core.database import Base, get_db
from app.core.deps import current_user
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.ssh_ca import SshCertificateAuthority

CA_KEY = ed25519.Ed25519PrivateKey.generate()
CLIENT_KEY = ed25519.Ed25519PrivateKey.generate()
CA_PRIVATE_KEY = CA_KEY.private_bytes(
    Encoding.PEM,
    PrivateFormat.OpenSSH,
    NoEncryption(),
).decode()
CLIENT_PUBLIC_KEY = CLIENT_KEY.public_key().public_bytes(
    Encoding.OpenSSH,
    PublicFormat.OpenSSH,
).decode()


class StaticSecretProvider:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def unwrap(self, secret_id: str) -> str:
        secret = self._secrets.get(secret_id)
        if secret is None:
            raise ValueError("SECRET_NOT_FOUND")
        return secret


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def install_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db


def install_ssh_ca_secrets(secrets: dict[str, str] | None = None) -> None:
    secret_values = secrets if secrets is not None else {"sec_tenant_a_ssh_ca": CA_PRIVATE_KEY}
    app.dependency_overrides[get_ssh_ca_secret_provider] = lambda: StaticSecretProvider(
        secret_values
    )


def install_user(
    *,
    tenant_id: str,
    permissions: list[str],
    organization_id: str | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": organization_id,
        "team_id": team_id,
        "project_id": project_id,
        "permissions": permissions,
    }


async def seed_ssh_ca_fixture(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            SshCertificateAuthority(
                id=1,
                tenant_id="tenant-a",
                name="tenant-a-ca",
                public_key="ssh-ed25519 AAAAC3NzaTenantA",
                private_key_secret_id="sec_tenant_a_ssh_ca",
                validity_seconds=900,
            )
        )
        session.add(
            SshCertificateAuthority(
                id=2,
                tenant_id="tenant-a",
                name="tenant-a-untrusted-ca",
                public_key="ssh-ed25519 AAAAC3NzaTenantAUntrusted",
                private_key_secret_id="sec_tenant_a_untrusted_ssh_ca",
                validity_seconds=900,
            )
        )
        session.add(
            SshCertificateAuthority(
                id=3,
                tenant_id="tenant-b",
                name="tenant-b-ca",
                public_key="ssh-ed25519 AAAAC3NzaTenantB",
                private_key_secret_id="sec_tenant_b_ssh_ca",
                validity_seconds=900,
            )
        )
        session.add(
            Asset(
                id=1,
                tenant_id="tenant-a",
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                trusted_ssh_ca_id=1,
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
                status="active",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_ssh_certificate_api_issues_and_revokes_temporary_certificate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        issue_response = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 1,
                "asset_id": 1,
                "account_id": 1,
                "principal": "deploy",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )
        list_response = client.get("/api/v1/ssh-certificates/")
        revoke_response = client.post(
            "/api/v1/ssh-certificates/1/revoke",
            json={"reason": "access ended"},
        )

    assert issue_response.status_code == 201
    issued = issue_response.json()
    assert issued["tenant_id"] == "tenant-a"
    assert issued["ca_id"] == 1
    assert issued["asset_id"] == 1
    assert issued["account_id"] == 1
    assert issued["principal"] == "deploy"
    assert issued["status"] == "issued"
    parsed_certificate = load_ssh_public_identity(issued["certificate_body"].encode())
    assert issued["certificate_body"].startswith("ssh-ed25519-cert-v01@openssh.com ")
    assert parsed_certificate.valid_principals == [b"deploy"]
    assert "private_key" not in issued
    assert "private_key_secret_id" not in issued
    assert list_response.status_code == 200
    assert list_response.json() == {"items": [issued], "total": 1}
    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "revoked"
    assert revoke_response.json()["revoke_reason"] == "access ended"


@pytest.mark.asyncio
async def test_ssh_ca_management_api_lists_and_creates_tenant_authorities(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["ssh-certificate-authorities:read"])
        list_response = client.get("/api/v1/ssh-certificate-authorities/")
        forbidden_create = client.post(
            "/api/v1/ssh-certificate-authorities/",
            json={
                "name": "tenant-a-new-ca",
                "public_key": "ssh-ed25519 AAAAC3NzaTenantANew",
                "private_key_secret_id": "sec_tenant_a_new_ssh_ca",
                "validity_seconds": 1200,
            },
        )

        install_user(tenant_id="tenant-a", permissions=["ssh-certificate-authorities:create"])
        create_response = client.post(
            "/api/v1/ssh-certificate-authorities/",
            json={
                "name": "tenant-a-new-ca",
                "public_key": "ssh-ed25519 AAAAC3NzaTenantANew",
                "private_key_secret_id": "sec_tenant_a_new_ssh_ca",
                "validity_seconds": 1200,
            },
        )

    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] == 2
    assert {item["name"] for item in listed["items"]} == {
        "tenant-a-ca",
        "tenant-a-untrusted-ca",
    }
    assert all(item["tenant_id"] == "tenant-a" for item in listed["items"])
    assert all("private_key_secret_id" not in item for item in listed["items"])
    assert forbidden_create.status_code == 403
    assert forbidden_create.json()["code"] == "FORBIDDEN"
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["name"] == "tenant-a-new-ca"
    assert created["public_key"] == "ssh-ed25519 AAAAC3NzaTenantANew"
    assert created["validity_seconds"] == 1200
    assert created["status"] == "active"
    assert "private_key_secret_id" not in created


@pytest.mark.asyncio
async def test_ssh_ca_management_api_disables_tenant_authority_without_leaking_secret(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["ssh-certificate-authorities:read"])
        forbidden_disable = client.post("/api/v1/ssh-certificate-authorities/1/disable")

        install_user(tenant_id="tenant-b", permissions=["ssh-certificate-authorities:disable"])
        cross_tenant_disable = client.post("/api/v1/ssh-certificate-authorities/1/disable")

        install_user(tenant_id="tenant-a", permissions=["ssh-certificate-authorities:disable"])
        disable_response = client.post("/api/v1/ssh-certificate-authorities/1/disable")
        disable_again = client.post("/api/v1/ssh-certificate-authorities/1/disable")

    assert forbidden_disable.status_code == 403
    assert forbidden_disable.json()["code"] == "FORBIDDEN"
    assert cross_tenant_disable.status_code == 404
    assert cross_tenant_disable.json()["code"] == "SSH_CA_NOT_FOUND"
    assert disable_response.status_code == 200
    disabled = disable_response.json()
    assert disabled["tenant_id"] == "tenant-a"
    assert disabled["id"] == 1
    assert disabled["status"] == "disabled"
    assert "private_key_secret_id" not in disabled
    assert "private_key" not in disabled
    assert disable_again.status_code == 404
    assert disable_again.json()["code"] == "SSH_CA_NOT_FOUND"

    async with session_factory() as session:
        authority = await session.get(SshCertificateAuthority, 1)
    assert authority is not None
    assert authority.status == "disabled"


@pytest.mark.asyncio
async def test_ssh_ca_trust_bundle_lists_active_tenant_asset_trust_without_secrets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    async with session_factory() as session:
        session.add(
            SshCertificateAuthority(
                id=4,
                tenant_id="tenant-a",
                name="tenant-a-disabled-ca",
                public_key="ssh-ed25519 AAAAC3NzaTenantADisabled",
                private_key_secret_id="sec_tenant_a_disabled_ssh_ca",
                status="disabled",
                validity_seconds=900,
            )
        )
        session.add(
            Asset(
                id=2,
                tenant_id="tenant-a",
                name="disabled-ca-host",
                address="203.0.113.11",
                platform_id=1,
                trusted_ssh_ca_id=4,
            )
        )
        session.add(
            Asset(
                id=3,
                tenant_id="tenant-a",
                name="second-prod-linux",
                address="203.0.113.12",
                platform_id=1,
                trusted_ssh_ca_id=1,
            )
        )
        session.add(
            Asset(
                id=4,
                tenant_id="tenant-a",
                name="inactive-prod-linux",
                address="203.0.113.13",
                platform_id=1,
                trusted_ssh_ca_id=1,
                is_active=False,
            )
        )
        await session.commit()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[])
        forbidden = client.get("/api/v1/ssh-certificate-authorities/trust-bundle")

        install_user(tenant_id="tenant-a", permissions=["ssh-certificate-authorities:read"])
        response = client.get("/api/v1/ssh-certificate-authorities/trust-bundle")

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "ca_id": 1,
                "tenant_id": "tenant-a",
                "name": "tenant-a-ca",
                "public_key": "ssh-ed25519 AAAAC3NzaTenantA",
                "trusted_asset_ids": [1, 3],
            }
        ],
        "total": 1,
    }
    assert "private_key_secret_id" not in response.text
    assert "sec_tenant" not in response.text


@pytest.mark.asyncio
async def test_ssh_certificate_api_maps_service_errors_to_stable_codes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["ssh-certificates:issue"])
        untrusted_response = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 2,
                "asset_id": 1,
                "account_id": 1,
                "principal": "deploy",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )

        install_user(tenant_id="tenant-b", permissions=["ssh-certificates:issue"])
        cross_tenant_response = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 1,
                "asset_id": 1,
                "account_id": 1,
                "principal": "deploy",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )

    assert untrusted_response.status_code == 403
    assert untrusted_response.json()["code"] == "ASSET_SSH_CA_NOT_TRUSTED"
    assert cross_tenant_response.status_code == 404
    assert cross_tenant_response.json()["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_ssh_certificate_api_requires_permissions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["ssh-certificates:read"])
        issue_response = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 1,
                "asset_id": 1,
                "account_id": 1,
                "principal": "deploy",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )
        revoke_response = client.post(
            "/api/v1/ssh-certificates/1/revoke",
            json={"reason": "access ended"},
        )

    assert issue_response.status_code == 403
    assert issue_response.json()["code"] == "FORBIDDEN"
    assert revoke_response.status_code == 403
    assert revoke_response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_ssh_certificate_api_respects_account_project_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets()
    await seed_ssh_ca_fixture(session_factory)

    async with session_factory() as session:
        session.add(
            Account(
                id=2,
                tenant_id="tenant-a",
                asset_id=1,
                username="breakglass",
                protocol="ssh",
                secret_id="sec_tenant_a_breakglass",
                project_id="project-b",
                status="active",
            )
        )
        await session.commit()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        first = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 1,
                "asset_id": 1,
                "account_id": 1,
                "principal": "deploy",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )
        second = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 1,
                "asset_id": 1,
                "account_id": 2,
                "principal": "breakglass",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )

        install_user(
            tenant_id="tenant-a",
            permissions=["ssh-certificates:read", "ssh-certificates:revoke"],
            project_id="project-b",
        )
        list_response = client.get("/api/v1/ssh-certificates/")
        revoke_out_of_scope = client.post(
            f"/api/v1/ssh-certificates/{first.json()['id']}/revoke",
            json={"reason": "wrong project"},
        )
        revoke_in_scope = client.post(
            f"/api/v1/ssh-certificates/{second.json()['id']}/revoke",
            json={"reason": "project owner ended access"},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["account_id"] == 2
    assert revoke_out_of_scope.status_code == 404
    assert revoke_out_of_scope.json()["code"] == "SSH_CERTIFICATE_NOT_FOUND"
    assert revoke_in_scope.status_code == 200
    assert revoke_in_scope.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_ssh_certificate_api_fails_closed_when_ca_secret_is_unavailable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_ssh_ca_secrets({})
    await seed_ssh_ca_fixture(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["ssh-certificates:issue"])
        response = client.post(
            "/api/v1/ssh-certificates/",
            json={
                "ca_id": 1,
                "asset_id": 1,
                "account_id": 1,
                "principal": "deploy",
                "public_key": CLIENT_PUBLIC_KEY,
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "SSH_CA_PRIVATE_KEY_UNAVAILABLE"
