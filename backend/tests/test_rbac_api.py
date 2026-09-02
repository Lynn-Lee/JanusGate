"""#t63 RBAC API 与权限解析回归测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.rbac import RoleBindingModel, RoleModel, UserGroupModel
from app.models.user import User
from app.policy.rbac import RbacService, dump_json_list, parse_json_list


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


def install_user(*, tenant_id: str, permissions: list[str], user_id: str = "42") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
        "group_ids": [],
    }


async def seed_user(session_factory: async_sessionmaker[AsyncSession], *, user_id: int) -> None:
    async with session_factory() as session:
        session.add(
            User(
                id=user_id,
                username=f"user-{user_id}",
                password_hash="hash",
                tenant_id="tenant-a",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_rbac_api_lists_builtin_roles_and_requires_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["assets:read"])
        forbidden = client.get("/api/v1/rbac/roles")
        assert forbidden.status_code == 403

        install_user(tenant_id="tenant-a", permissions=["rbac:read"])
        listed = client.get("/api/v1/rbac/roles")
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["total"] >= 4
        builtin_keys = {item["builtin_key"] for item in payload["items"]}
        assert {"system_admin", "org_admin", "auditor", "user"}.issubset(builtin_keys)


@pytest.mark.asyncio
async def test_role_binding_grants_auditor_permissions_via_group_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_user(session_factory, user_id=42)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["rbac:write", "rbac:read"], user_id="99")
        group = client.post(
            "/api/v1/rbac/user-groups",
            json={"name": "audit-team", "member_ids": ["42"]},
        )
        assert group.status_code == 201
        group_id = group.json()["id"]

        roles = client.get("/api/v1/rbac/roles")
        auditor_role = next(item for item in roles.json()["items"] if item["builtin_key"] == "auditor")
        binding = client.post(
            "/api/v1/rbac/role-bindings",
            json={
                "role_id": auditor_role["id"],
                "subject_type": "user_group",
                "subject_id": group_id,
            },
        )
        assert binding.status_code == 201

        install_user(tenant_id="tenant-a", permissions=["assets:read"], user_id="42")
        effective = client.get("/api/v1/rbac/me/effective")
        assert effective.status_code == 200
        body = effective.json()
        assert "audit:read" in body["permissions"]
        assert "workflow:audit" in body["permissions"]
        assert group_id in body["group_ids"]


@pytest.mark.asyncio
async def test_rbac_service_object_permission_allows_group_subject(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as session:
        await RbacService.ensure_builtin_roles(session, "tenant-a")
        group = UserGroupModel(
            id="group-ops",
            tenant_id="tenant-a",
            name="ops",
            member_ids_json=dump_json_list(["42"]),
        )
        session.add(group)
        session.add(
            RoleModel(
                id="role-custom",
                tenant_id="tenant-a",
                name="custom",
                scope="org",
                permissions_json=dump_json_list(["assets:write"]),
                menu_permissions_json="[]",
            )
        )
        await session.commit()

    async with session_factory() as session:
        from app.tenancy.scope import ActorScope

        actor_scope = ActorScope(user_id="42", tenant_id="tenant-a")
        group_ids = await RbacService.list_group_ids_for_user(session, actor_scope, "42")
        assert group_ids == ("group-ops",)

        session.add(
            RoleBindingModel(
                id="binding-1",
                tenant_id="tenant-a",
                role_id="role-custom",
                subject_type="user_group",
                subject_id="group-ops",
            )
        )
        await session.commit()

        effective = await RbacService.resolve_effective_rbac(
            session, user_id="42", tenant_id="tenant-a"
        )
        assert "assets:write" in effective.permissions
        assert effective.group_ids == ("group-ops",)


@pytest.mark.asyncio
async def test_rbac_tenant_isolation_returns_404_for_foreign_group_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as session:
        await RbacService.ensure_builtin_roles(session, "tenant-a")
        await RbacService.ensure_builtin_roles(session, "tenant-b")
        session.add(
            UserGroupModel(
                id="group-b",
                tenant_id="tenant-b",
                name="other",
                member_ids_json="[]",
            )
        )
        session.add(
            RoleModel(
                id="role-a",
                tenant_id="tenant-a",
                name="tenant-a-role",
                scope="org",
                permissions_json="[]",
                menu_permissions_json="[]",
            )
        )
        await session.commit()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["rbac:write"])
        response = client.post(
            "/api/v1/rbac/role-bindings",
            json={
                "role_id": "role-a",
                "subject_type": "user_group",
                "subject_id": "group-b",
            },
        )
        assert response.status_code == 404


def test_parse_json_list_rejects_invalid_payload() -> None:
    assert parse_json_list("not-json") == []
    assert parse_json_list('{"bad": true}') == []
