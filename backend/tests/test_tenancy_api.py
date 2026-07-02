"""Phase 4 tenancy management API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.deps import current_user
from app.main import app


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


@pytest.mark.asyncio
async def test_tenancy_organization_api_is_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        create_response = client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-a", "name": "Tenant A Ops"},
        )
        tenant_a_list = client.get("/api/v1/tenancy/organizations")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/tenancy/organizations")

    assert create_response.status_code == 201
    assert create_response.json()["tenant_id"] == "tenant-a"
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {
        "items": [
            {
                "id": "org-a",
                "tenant_id": "tenant-a",
                "name": "Tenant A Ops",
                "status": "active",
            }
        ],
        "total": 1,
    }
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_tenancy_create_organization_requires_admin_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["tenancy:read"])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-a", "name": "Tenant A Ops"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_tenancy_team_api_is_tenant_and_team_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-a", "name": "Tenant A Ops"},
        )
        create_response = client.post(
            "/api/v1/tenancy/teams",
            json={"id": "team-a", "organization_id": "org-a", "name": "Ops"},
        )
        client.post(
            "/api/v1/tenancy/teams",
            json={"id": "team-b", "organization_id": "org-a", "name": "Security"},
        )

        install_user(
            tenant_id="tenant-a",
            permissions=["tenancy:read"],
            organization_id="org-a",
            team_id="team-a",
        )
        scoped_list = client.get("/api/v1/tenancy/teams")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/tenancy/teams")

    assert create_response.status_code == 201
    assert create_response.json() == {
        "id": "team-a",
        "tenant_id": "tenant-a",
        "organization_id": "org-a",
        "name": "Ops",
    }
    assert scoped_list.status_code == 200
    assert scoped_list.json() == {
        "items": [
            {
                "id": "team-a",
                "tenant_id": "tenant-a",
                "organization_id": "org-a",
                "name": "Ops",
            }
        ],
        "total": 1,
    }
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_tenancy_create_team_rejects_cross_tenant_organization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-a", "name": "Tenant A Ops"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        response = client.post(
            "/api/v1/tenancy/teams",
            json={"id": "team-a", "organization_id": "org-a", "name": "Ops"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_SCOPE_VIOLATION"


@pytest.mark.asyncio
async def test_tenancy_project_api_is_tenant_and_project_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-a", "name": "Tenant A Ops"},
        )
        client.post(
            "/api/v1/tenancy/teams",
            json={"id": "team-a", "organization_id": "org-a", "name": "Ops"},
        )
        create_response = client.post(
            "/api/v1/tenancy/projects",
            json={
                "id": "project-a",
                "organization_id": "org-a",
                "team_id": "team-a",
                "name": "Production",
            },
        )
        client.post(
            "/api/v1/tenancy/projects",
            json={
                "id": "project-b",
                "organization_id": "org-a",
                "name": "Security Lab",
            },
        )

        install_user(
            tenant_id="tenant-a",
            permissions=["tenancy:read"],
            organization_id="org-a",
            team_id="team-a",
            project_id="project-a",
        )
        scoped_list = client.get("/api/v1/tenancy/projects")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/tenancy/projects")

    assert create_response.status_code == 201
    assert create_response.json() == {
        "id": "project-a",
        "tenant_id": "tenant-a",
        "organization_id": "org-a",
        "team_id": "team-a",
        "name": "Production",
        "status": "active",
    }
    assert scoped_list.status_code == 200
    assert scoped_list.json() == {
        "items": [
            {
                "id": "project-a",
                "tenant_id": "tenant-a",
                "organization_id": "org-a",
                "team_id": "team-a",
                "name": "Production",
                "status": "active",
            }
        ],
        "total": 1,
    }
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_tenancy_create_project_rejects_cross_tenant_team(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-a", "name": "Tenant A Ops"},
        )
        client.post(
            "/api/v1/tenancy/teams",
            json={"id": "team-a", "organization_id": "org-a", "name": "Ops"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        client.post(
            "/api/v1/tenancy/organizations",
            json={"id": "org-b", "name": "Tenant B Ops"},
        )
        response = client.post(
            "/api/v1/tenancy/projects",
            json={
                "id": "project-b",
                "organization_id": "org-b",
                "team_id": "team-a",
                "name": "Production",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_SCOPE_VIOLATION"
