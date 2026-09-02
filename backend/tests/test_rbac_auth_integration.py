"""#t63 登录签发 token 时接入 RBAC。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.security import hash_password
from app.main import app
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


@pytest.mark.asyncio
async def test_login_issues_rbac_permissions_for_superuser(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            User(
                username="root",
                password_hash=hash_password("Password123!"),
                is_superuser=True,
                tenant_id="tenant-a",
            )
        )
        await session.commit()

    install_db(session_factory)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "root", "password": "Password123!"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        assert token

    from app.core.security import decode_token

    payload = decode_token(token)
    assert "admin" in payload["permissions"]
    assert "rbac:manage" in payload["permissions"]
    assert "menu_permissions" in payload
    assert "settings" in payload["menu_permissions"]


@pytest.mark.asyncio
async def test_login_issues_default_user_permissions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            User(
                username="alice",
                password_hash=hash_password("Password123!"),
                is_superuser=False,
                tenant_id="tenant-a",
            )
        )
        await session.commit()

    install_db(session_factory)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "Password123!"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

    from app.core.security import decode_token

    payload = decode_token(token)
    assert payload["permissions"] == ["assets:read", "sessions:connect"]
    assert payload["menu_permissions"] == ["assets", "dashboard", "sessions"]
