"""#t63 RBAC 管理 API 契约测试。"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
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
    app.dependency_overrides[get_read_db] = override_db


def install_user(*, tenant_id: str, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-1",
        "username": "root",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_roles_returns_builtin_roles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="default", permissions=["rbac:read"])
        response = client.get("/api/v1/rbac/roles")

    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert {"system_admin", "org_admin", "auditor", "user"} <= ids
    system_admin = next(item for item in body["items"] if item["id"] == "system_admin")
    assert system_admin["builtin"] is True
    assert "admin" in system_admin["permissions"]


@pytest.mark.asyncio
async def test_role_read_requires_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="default", permissions=["assets:read"])
        response = client.get("/api/v1/rbac/roles")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_create_custom_role_requires_rbac_admin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="default", permissions=["rbac:read"])
        forbidden = client.post(
            "/api/v1/rbac/roles",
            json={"name": "Ops", "permissions": ["assets:read"]},
        )

        install_user(tenant_id="default", permissions=["rbac:admin"])
        created = client.post(
            "/api/v1/rbac/roles",
            json={"name": "Ops", "scope": "system", "permissions": ["assets:read", "assets:read"]},
        )

    assert forbidden.status_code == 403
    assert created.status_code == 201
    body = created.json()
    assert body["builtin"] is False
    assert body["permissions"] == ["assets:read"]
    assert body["id"].startswith("role_")


@pytest.mark.asyncio
async def test_create_role_binding_and_tenant_scoped_list(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post(
            "/api/v1/rbac/role-bindings",
            json={"user_id": "42", "role_id": "auditor"},
        )
        tenant_a_list = client.get("/api/v1/rbac/role-bindings")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/rbac/role-bindings")

    assert created.status_code == 201
    binding = created.json()
    assert binding["role_id"] == "auditor"
    assert binding["tenant_id"] == "tenant-a"
    assert binding["organization_id"] == ""
    assert tenant_a_list.json()["total"] == 1
    assert tenant_b_list.json()["total"] == 0


@pytest.mark.asyncio
async def test_create_role_binding_rejects_unknown_role(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="default", permissions=["rbac:admin"])
        response = client.post(
            "/api/v1/rbac/role-bindings",
            json={"user_id": "42", "role_id": "does-not-exist"},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "ROLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_org_scoped_binding_requires_organization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="default", permissions=["rbac:admin"])
        response = client.post(
            "/api/v1/rbac/role-bindings",
            json={"user_id": "42", "role_id": "org_admin", "scope_type": "org"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "ORGANIZATION_REQUIRED"


@pytest.mark.asyncio
async def test_delete_role_binding_is_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post(
            "/api/v1/rbac/role-bindings",
            json={"user_id": "42", "role_id": "auditor"},
        )
        binding_id = created.json()["id"]

        install_user(tenant_id="tenant-b", permissions=["admin"])
        cross_tenant = client.delete(f"/api/v1/rbac/role-bindings/{binding_id}")

        install_user(tenant_id="tenant-a", permissions=["admin"])
        deleted = client.delete(f"/api/v1/rbac/role-bindings/{binding_id}")
        empty = client.get("/api/v1/rbac/role-bindings")

    assert cross_tenant.status_code == 404
    assert deleted.status_code == 204
    assert empty.json()["total"] == 0
