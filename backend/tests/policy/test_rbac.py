"""#t63 RBAC 权限解析单元测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.rbac import RbacSubjectType, RoleBindingModel, RoleModel
from app.policy.rbac import (
    ADMIN_CONSOLE_PERMISSIONS,
    MVP_CONSOLE_PERMISSIONS,
    RbacService,
    dump_json_list,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_superuser_fallback_without_bindings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        effective = await RbacService.resolve_effective_rbac(
            session, user_id="1", tenant_id="tenant-a", is_superuser=True
        )
    assert set(ADMIN_CONSOLE_PERMISSIONS).issubset(set(effective.permissions))


@pytest.mark.asyncio
async def test_regular_user_fallback_without_bindings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        effective = await RbacService.resolve_effective_rbac(
            session, user_id="1", tenant_id="tenant-a", is_superuser=False
        )
    assert set(MVP_CONSOLE_PERMISSIONS).issubset(set(effective.permissions))


@pytest.mark.asyncio
async def test_user_binding_overrides_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await RbacService.ensure_builtin_roles(session, "tenant-a")
        result = await session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id == "tenant-a",
                RoleModel.builtin_key == "auditor",
            )
        )
        auditor = result.scalar_one()
        session.add(
            RoleBindingModel(
                id="bind-auditor",
                tenant_id="tenant-a",
                role_id=auditor.id,
                subject_type=RbacSubjectType.USER,
                subject_id="7",
            )
        )
        await session.commit()

        effective = await RbacService.resolve_effective_rbac(
            session, user_id="7", tenant_id="tenant-a", is_superuser=False
        )
    assert "audit:read" in effective.permissions
    assert "assets:write" not in effective.permissions


def test_permissions_include_admin_shortcut() -> None:
    assert RbacService.permissions_include(["admin"], "rbac:write") is True
    assert RbacService.permissions_include(["assets:read"], "assets:read") is True
    assert RbacService.permissions_include(["assets:read"], "assets:write") is False


def test_dump_json_list_roundtrip() -> None:
    assert dump_json_list(["a", "b"]) == '["a", "b"]'
