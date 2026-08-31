"""#t63 RBAC 权限解析服务测试。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.rbac import Role, RoleBinding
from app.services.rbac import RbacService


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
async def test_no_bindings_superuser_gets_system_admin_baseline(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        permissions = await RbacService.resolve_effective_permissions(
            session, user_id="1", tenant_id="default", is_superuser=True
        )
    assert "admin" in permissions
    assert "audit:write" in permissions


@pytest.mark.asyncio
async def test_no_bindings_regular_user_gets_default_baseline(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        permissions = await RbacService.resolve_effective_permissions(
            session, user_id="7", tenant_id="default", is_superuser=False
        )
    assert permissions == ["assets:read"]


@pytest.mark.asyncio
async def test_builtin_role_binding_adds_permissions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            RoleBinding(
                id="rb-1",
                tenant_id="default",
                user_id="7",
                role_id="auditor",
                scope_type="system",
                organization_id="",
            )
        )
        await session.commit()
        permissions = await RbacService.resolve_effective_permissions(
            session, user_id="7", tenant_id="default", is_superuser=False
        )
    # 回退基线 assets:read 与 auditor 的 audit:read 并集。
    assert "audit:read" in permissions
    assert "assets:read" in permissions
    assert "admin" not in permissions


@pytest.mark.asyncio
async def test_custom_role_binding_adds_permissions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Role(
                id="role-x",
                tenant_id="default",
                name="Session Operator",
                scope="system",
                permissions_json=json.dumps(["sessions:connect", "assets:read"]),
                description="",
            )
        )
        session.add(
            RoleBinding(
                id="rb-2",
                tenant_id="default",
                user_id="9",
                role_id="role-x",
                scope_type="system",
                organization_id="",
            )
        )
        await session.commit()
        permissions = await RbacService.resolve_effective_permissions(
            session, user_id="9", tenant_id="default", is_superuser=False
        )
    assert "sessions:connect" in permissions


@pytest.mark.asyncio
async def test_binding_in_other_tenant_is_not_applied(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            RoleBinding(
                id="rb-3",
                tenant_id="tenant-b",
                user_id="9",
                role_id="system_admin",
                scope_type="system",
                organization_id="",
            )
        )
        await session.commit()
        permissions = await RbacService.resolve_effective_permissions(
            session, user_id="9", tenant_id="tenant-a", is_superuser=False
        )
    assert "admin" not in permissions
    assert permissions == ["assets:read"]
