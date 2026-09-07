"""#t73 账号模板 CRUD：权限、删除 SET NULL unlink、协议固定 ssh。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.account import Account, AccountTemplate
from app.models.asset import Asset, Platform


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


async def seed_asset(session_factory: async_sessionmaker[AsyncSession]) -> Asset:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        asset = Asset(id=1, name="prod", address="203.0.113.10", platform_id=1, tenant_id="tenant-a")
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset


@pytest.mark.asyncio
async def test_account_template_crud_and_delete_unlinks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["accounts:read", "accounts:write"])
    await seed_asset(session_factory)

    with TestClient(app) as client:
        empty = client.get("/api/v1/account-templates/")
        assert empty.status_code == 200
        assert empty.json()["total"] == 0

        created = client.post(
            "/api/v1/account-templates/",
            json={"name": "运维账号", "default_username": "ops"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "运维账号"
        assert body["protocol"] == "ssh"
        assert body["default_username"] == "ops"
        template_id = body["id"]

        patched = client.patch(
            f"/api/v1/account-templates/{template_id}",
            json={"name": "运维账号-改", "default_username": "deploy"},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "运维账号-改"
        assert patched.json()["protocol"] == "ssh"
        assert patched.json()["default_username"] == "deploy"

    async with session_factory() as session:
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_1",
                template_id=template_id,
            )
        )
        await session.commit()

    with TestClient(app) as client:
        deleted = client.delete(f"/api/v1/account-templates/{template_id}")
        assert deleted.status_code == 204

    async with session_factory() as session:
        assert await session.get(AccountTemplate, template_id) is None
        account = (await session.execute(__import__("sqlalchemy").select(Account))).scalar_one()
        assert account.template_id is None


@pytest.mark.asyncio
async def test_account_template_permission_denied(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=[])
    with TestClient(app) as client:
        denied = client.get("/api/v1/account-templates/")
        assert denied.status_code == 403
        assert "没有权限" not in str(denied.json())
