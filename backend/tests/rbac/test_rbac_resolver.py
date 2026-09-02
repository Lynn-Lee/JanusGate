"""#t63 RBAC 解析器单元测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.rbac import RoleBindingModel, RoleModel, RoleObjectPermissionModel
from app.rbac.constants import BUILTIN_AUDITOR, BUILTIN_USER
from app.rbac.ops import dump_json_list, new_binding_id, new_object_permission_id, new_role_id
from app.rbac.repository import ensure_builtin_roles
from app.rbac.resolver import RbacResolver
from app.tenancy.scope import ActorScope


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_builtin_roles_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first = await ensure_builtin_roles(session, "tenant-a")
        second = await ensure_builtin_roles(session, "tenant-a")
        assert len(first) == 4
        assert second == []
        roles = await RbacResolver.list_roles(
            session, ActorScope(user_id="u1", tenant_id="tenant-a", permissions=("admin",))
        )
        assert len(roles) == 4
        builtin_keys = {role.builtin_key for role in roles}
        assert builtin_keys == {BUILTIN_USER, BUILTIN_AUDITOR, "org_admin", "system_admin"}


@pytest.mark.asyncio
async def test_resolve_default_user_permissions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ensure_builtin_roles(session, "tenant-a")
        user_role = RoleModel(
            id=new_role_id(),
            tenant_id="tenant-a",
            name="user",
            display_name="普通用户",
            scope_type="system",
            is_builtin=True,
            builtin_key=BUILTIN_USER,
            permissions_json=dump_json_list(["assets:read", "sessions:connect"]),
            menu_permissions_json=dump_json_list(["dashboard", "assets", "sessions"]),
        )
        session.add(user_role)
        session.add(
            RoleBindingModel(
                id=new_binding_id(),
                tenant_id="tenant-a",
                role_id=user_role.id,
                subject_type="user",
                subject_id="42",
                scope_type="system",
            )
        )
        await session.commit()

        effective = await RbacResolver.resolve(
            session,
            actor_scope=ActorScope(user_id="42", tenant_id="tenant-a"),
        )
        assert "assets:read" in effective.permissions
        assert "sessions:connect" in effective.permissions
        assert "dashboard" in effective.menu_permissions


@pytest.mark.asyncio
async def test_resolve_org_scope_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        role = RoleModel(
            id=new_role_id(),
            tenant_id="tenant-a",
            name="org_reader",
            display_name="组织只读",
            scope_type="organization",
            organization_id="org-1",
            permissions_json=dump_json_list(["tenancy:read"]),
            menu_permissions_json=dump_json_list(["tenancy"]),
        )
        session.add(role)
        session.add(
            RoleBindingModel(
                id=new_binding_id(),
                tenant_id="tenant-a",
                role_id=role.id,
                subject_type="user",
                subject_id="7",
                scope_type="organization",
                organization_id="org-1",
            )
        )
        await session.commit()

        effective = await RbacResolver.resolve(
            session,
            actor_scope=ActorScope(
                user_id="7",
                tenant_id="tenant-a",
                organization_ids=("org-1",),
            ),
        )
        assert effective.permissions == ("tenancy:read",)
        assert effective.menu_permissions == ("tenancy",)

        denied = await RbacResolver.resolve(
            session,
            actor_scope=ActorScope(
                user_id="7",
                tenant_id="tenant-a",
                organization_ids=("org-2",),
            ),
        )
        assert "tenancy:read" not in denied.permissions


@pytest.mark.asyncio
async def test_resolve_user_group_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        role = RoleModel(
            id=new_role_id(),
            tenant_id="tenant-a",
            name="auditor",
            display_name="审计员",
            scope_type="system",
            permissions_json=dump_json_list(["audit:read"]),
            menu_permissions_json=dump_json_list(["audits"]),
        )
        session.add(role)
        session.add(
            RoleBindingModel(
                id=new_binding_id(),
                tenant_id="tenant-a",
                role_id=role.id,
                subject_type="user_group",
                subject_id="auditors",
                scope_type="system",
            )
        )
        await session.commit()

        effective = await RbacResolver.resolve(
            session,
            actor_scope=ActorScope(user_id="9", tenant_id="tenant-a"),
            group_ids=("auditors",),
        )
        assert effective.permissions == ("audit:read",)


@pytest.mark.asyncio
async def test_resolve_object_permission_for_organization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        role = RoleModel(
            id=new_role_id(),
            tenant_id="tenant-a",
            name="scoped",
            display_name="范围权限",
            scope_type="system",
            permissions_json=dump_json_list([]),
            menu_permissions_json=dump_json_list([]),
        )
        session.add(role)
        session.add(
            RoleBindingModel(
                id=new_binding_id(),
                tenant_id="tenant-a",
                role_id=role.id,
                subject_type="user",
                subject_id="3",
                scope_type="system",
            )
        )
        session.add(
            RoleObjectPermissionModel(
                id=new_object_permission_id(),
                tenant_id="tenant-a",
                role_id=role.id,
                resource_type="organization",
                resource_id="org-9",
                action="tenancy:read",
            )
        )
        await session.commit()

        effective = await RbacResolver.resolve(
            session,
            actor_scope=ActorScope(
                user_id="3",
                tenant_id="tenant-a",
                organization_ids=("org-9",),
            ),
        )
        assert effective.permissions == ("tenancy:read",)
        assert len(effective.object_permissions) == 1
