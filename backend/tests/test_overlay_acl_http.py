"""#t65 overlay ACL HTTP：交互式登录拒绝文案、会话连接 overlay。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.security import hash_password
from app.main import app
from app.models.acl import LoginAclModel, LoginAssetAclModel, OverlayAclAction
from app.models.asset import Asset
from app.models.asset_tree import ASSET_RESOURCE, CONNECT_ACTION, AssetPermissionModel
from app.models.user import User


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


def install_user(*, permissions: list[str], user_id: str = "user-1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": "tenant-a",
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


async def seed_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    async with session_factory() as session:
        user = User(
            username="alice",
            display_name="Alice",
            email="alice@example.test",
            password_hash=hash_password("correct-password"),
            tenant_id="tenant-a",
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_interactive_login_rejected_by_login_acl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    user = await seed_user(session_factory)
    async with session_factory() as session:
        session.add(
            LoginAclModel(
                id="la-1",
                tenant_id="tenant-a",
                name="block-alice",
                priority=10,
                action=OverlayAclAction.REJECT,
                subject_id=str(user.id),
            )
        )
        await session.commit()

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "alice", "password": "correct-password"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    body = response.json()
    assert body["message"] == "当前无法登录"
    assert "没有权限" not in str(body)


@pytest.mark.asyncio
async def test_interactive_login_without_login_acl_still_works(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_user(session_factory)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "alice", "password": "correct-password"},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_session_create_overlay_reject_is_unable_to_connect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as session:
        session.add(
            Asset(
                id=10,
                name="web-1",
                address="10.0.0.10",
                tenant_id="tenant-a",
                platform_id=1,
            )
        )
        session.add(
            AssetPermissionModel(
                id="ap-10",
                tenant_id="tenant-a",
                subject_id="user-1",
                subject_type="user",
                resource_type=ASSET_RESOURCE,
                resource_id="10",
                account_id="",
                protocol="",
                action=CONNECT_ACTION,
                expires_at=None,
            )
        )
        session.add(
            LoginAssetAclModel(
                id="laa-1",
                tenant_id="tenant-a",
                name="block-connect",
                priority=10,
                action=OverlayAclAction.REJECT,
                resource_type="asset",
                resource_id="10",
            )
        )
        await session.commit()
    install_user(permissions=["admin"])
    payload = {
        "asset_id": "10",
        "account_id": "root",
        "protocol": "ssh",
        "connection_token": "token-1",
    }
    try:
        with TestClient(app) as client:
            missing = client.post("/api/v1/sessions/", json=payload)
            # first request uses seeded overlay; create a no-perm path below
            denied = missing
        # AssetPermission missing case uses a different asset
        with TestClient(app) as client:
            not_found = client.post(
                "/api/v1/sessions/",
                json={**payload, "asset_id": "99"},
            )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 403
    assert denied.json()["message"] == "无法连接"
    assert "资产不存在" not in str(denied.json())
    assert "没有权限" not in str(denied.json())
    assert not_found.status_code == 404
    assert not_found.json()["message"] == "资产不存在"
