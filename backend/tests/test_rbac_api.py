"""#t63 RBAC 管理 API 测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.rbac import RoleBindingModel, RoleModel
from app.rbac.ops import dump_json_list, new_binding_id, new_role_id


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


def install_user(*, tenant_id: str, permissions: list[str], user_id: str = "user-1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.mark.asyncio
async def test_list_roles_seeds_builtin_and_requires_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[])
        denied = client.get("/api/v1/roles/")
        assert denied.status_code == 403

        install_user(tenant_id="tenant-a", permissions=["rbac:read"])
        listed = client.get("/api/v1/roles/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 4
        assert any(item["builtin_key"] == "system_admin" for item in listed.json()["items"])


@pytest.mark.asyncio
async def test_create_custom_role_and_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["rbac:manage", "rbac:read"])
        created = client.post(
            "/api/v1/roles/",
            json={
                "name": "ops",
                "display_name": "运维",
                "permissions": ["assets:read", "sessions:connect"],
                "menu_permissions": ["assets", "sessions"],
            },
        )
        assert created.status_code == 201
        role_id = created.json()["id"]

        binding = client.post(
            "/api/v1/role-bindings/",
            json={
                "role_id": role_id,
                "subject_type": "user",
                "subject_id": "bob",
            },
        )
        assert binding.status_code == 201
        assert binding.json()["subject_id"] == "bob"


@pytest.mark.asyncio
async def test_builtin_role_is_immutable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["rbac:manage", "rbac:read"])
        listed = client.get("/api/v1/roles/")
        builtin = next(item for item in listed.json()["items"] if item["builtin_key"] == "user")
        patched = client.patch(
            f"/api/v1/roles/{builtin['id']}",
            json={"display_name": "改名"},
        )
        assert patched.status_code == 400


@pytest.mark.asyncio
async def test_cross_tenant_role_lookup_returns_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        role = RoleModel(
            id=new_role_id(),
            tenant_id="tenant-b",
            name="secret",
            display_name="Secret",
            scope_type="system",
            permissions_json=dump_json_list(["assets:read"]),
            menu_permissions_json=dump_json_list([]),
        )
        session.add(role)
        await session.commit()

    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["rbac:read"])
        missing = client.get(f"/api/v1/roles/{role.id}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_effective_permissions_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        role = RoleModel(
            id=new_role_id(),
            tenant_id="tenant-a",
            name="viewer",
            display_name="查看者",
            scope_type="system",
            permissions_json=dump_json_list(["assets:read"]),
            menu_permissions_json=dump_json_list(["assets"]),
        )
        session.add(role)
        session.add(
            RoleBindingModel(
                id=new_binding_id(),
                tenant_id="tenant-a",
                role_id=role.id,
                subject_type="user",
                subject_id="user-1",
                scope_type="system",
            )
        )
        await session.commit()

    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[], user_id="user-1")
        effective = client.get("/api/v1/rbac/effective")
        assert effective.status_code == 200
        body = effective.json()
        assert "assets:read" in body["permissions"]
        assert "assets" in body["menu_permissions"]
