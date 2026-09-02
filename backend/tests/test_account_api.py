"""Phase 4 account custody API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.tenancy import Organization, Project, Team


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
    app.dependency_overrides[get_read_db] = override_db


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


async def seed_inventory_and_tenancy(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(Asset(id=1, name="prod-linux", address="203.0.113.10", platform_id=1))
        session.add(Organization(id="org-a", tenant_id="tenant-a", name="Tenant A Ops"))
        session.add(Team(id="team-a", tenant_id="tenant-a", organization_id="org-a", name="Ops"))
        session.add(
            Project(
                id="project-a",
                tenant_id="tenant-a",
                organization_id="org-a",
                team_id="team-a",
                name="Production",
            )
        )
        session.add(Organization(id="org-b", tenant_id="tenant-b", name="Tenant B Ops"))
        await session.commit()


@pytest.mark.asyncio
async def test_account_api_creates_and_lists_accounts_with_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_inventory_and_tenancy(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        create_response = client.post(
            "/api/v1/accounts/",
            json={
                "asset_id": 1,
                "username": "deploy",
                "protocol": "ssh",
                "secret_id": "sec_tenant_a_deploy",
                "organization_id": "org-a",
                "team_id": "team-a",
                "project_id": "project-a",
                "rotation_policy": "manual",
            },
        )
        tenant_a_list = client.get("/api/v1/accounts/")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/accounts/")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created == {
        "id": 1,
        "tenant_id": "tenant-a",
        "asset_id": 1,
        "username": "deploy",
        "protocol": "ssh",
        "secret_id": "sec_tenant_a_deploy",
        "organization_id": "org-a",
        "team_id": "team-a",
        "project_id": "project-a",
        "status": "active",
        "rotation_policy": "manual",
        "k8s_namespaces": [],
        "k8s_service_account": "default",
        "k8s_default_pod": "",
        "k8s_default_container": None,
        "k8s_use_short_lived_token": True,
        "k8s_token_ttl_seconds": 3600,
    }
    assert "plaintext" not in created
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {"items": [created], "total": 1}
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_account_list_respects_project_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_inventory_and_tenancy(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        client.post(
            "/api/v1/accounts/",
            json={
                "asset_id": 1,
                "username": "deploy",
                "protocol": "ssh",
                "secret_id": "sec_tenant_a_deploy",
                "organization_id": "org-a",
                "team_id": "team-a",
                "project_id": "project-a",
            },
        )
        client.post(
            "/api/v1/accounts/",
            json={
                "asset_id": 1,
                "username": "breakglass",
                "protocol": "ssh",
                "secret_id": "sec_tenant_a_breakglass",
                "organization_id": "org-a",
                "team_id": "team-a",
            },
        )

        install_user(
            tenant_id="tenant-a",
            permissions=["accounts:read"],
            organization_id="org-a",
            team_id="team-a",
            project_id="project-a",
        )
        response = client.get("/api/v1/accounts/")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["username"] == "deploy"


@pytest.mark.asyncio
async def test_account_create_rejects_cross_tenant_project(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_inventory_and_tenancy(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-b", permissions=["admin"])
        response = client.post(
            "/api/v1/accounts/",
            json={
                "asset_id": 1,
                "username": "deploy",
                "protocol": "ssh",
                "secret_id": "sec_tenant_b_deploy",
                "organization_id": "org-a",
                "team_id": "team-a",
                "project_id": "project-a",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_SCOPE_VIOLATION"


@pytest.mark.asyncio
async def test_account_rotation_api_schedules_and_lists_jobs_with_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_inventory_and_tenancy(session_factory)

    async with session_factory() as session:
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
                organization_id="org-a",
                team_id="team-a",
                project_id="project-a",
            )
        )
        await session.commit()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        create_response = client.post(
            "/api/v1/accounts/1/rotations",
            json={"reason": "quarterly rotation", "scheduled_at": "2026-07-04T10:00:00Z"},
        )
        list_response = client.get("/api/v1/accounts/1/rotations")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_response = client.get("/api/v1/accounts/1/rotations")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created == {
        "id": 1,
        "tenant_id": "tenant-a",
        "account_id": 1,
        "status": "scheduled",
        "reason": "quarterly rotation",
        "requested_by": "user-1",
        "scheduled_at": "2026-07-04T10:00:00Z",
    }
    assert "secret_id" not in created
    assert "plaintext" not in created
    assert list_response.status_code == 200
    assert list_response.json() == {"items": [created], "total": 1}
    assert tenant_b_response.status_code == 404
    assert tenant_b_response.json()["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_account_rotation_api_respects_project_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_inventory_and_tenancy(session_factory)

    async with session_factory() as session:
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
                organization_id="org-a",
                team_id="team-a",
                project_id="project-a",
            )
        )
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="breakglass",
                protocol="ssh",
                secret_id="sec_tenant_a_breakglass",
                organization_id="org-a",
                team_id="team-a",
            )
        )
        await session.commit()

    with TestClient(app) as client:
        install_user(
            tenant_id="tenant-a",
            permissions=["accounts:rotate"],
            organization_id="org-a",
            team_id="team-a",
            project_id="project-a",
        )
        in_scope_response = client.post(
            "/api/v1/accounts/1/rotations",
            json={"reason": "project scoped rotation"},
        )
        out_of_scope_response = client.post(
            "/api/v1/accounts/2/rotations",
            json={"reason": "should not cross project scope"},
        )

    assert in_scope_response.status_code == 201
    assert in_scope_response.json()["account_id"] == 1
    assert out_of_scope_response.status_code == 404
    assert out_of_scope_response.json()["code"] == "ACCOUNT_NOT_FOUND"
