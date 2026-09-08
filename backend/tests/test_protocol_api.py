"""#t66 协议目录与 Platform 协议约束测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Platform
from app.protocols.catalog import PROTOCOL_CATALOG, validate_protocol_for_asset
from app.protocols.repository import ensure_builtin_protocols, sync_platform_protocols
from app.protocols.validation import ProtocolValidationError, validate_asset_protocol_binding


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


def install_user() -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "username": "alice",
        "tenant_id": "tenant-a",
        "permissions": ["assets:read", "assets:write"],
    }


@pytest.mark.asyncio
async def test_catalog_has_nineteen_protocols() -> None:
    assert len(PROTOCOL_CATALOG) == 20  # 19 对标 + gpt 扩展位


@pytest.mark.asyncio
async def test_validate_protocol_for_asset_type() -> None:
    assert validate_protocol_for_asset("host", "ssh") is True
    assert validate_protocol_for_asset("database", "ssh") is False
    assert validate_protocol_for_asset("database", "mysql") is True


@pytest.mark.asyncio
async def test_ensure_builtin_protocols_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        first = await ensure_builtin_protocols(session)
        second = await ensure_builtin_protocols(session)
        assert len(first) == len(PROTOCOL_CATALOG)
        assert second == []


@pytest.mark.asyncio
async def test_validate_asset_platform_binding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ensure_builtin_protocols(session)
        platform = Platform(name="Linux", category="host", asset_type="host", protocols='["ssh"]')
        session.add(platform)
        await session.commit()
        await session.refresh(platform)
        await validate_asset_protocol_binding(
            session, asset_type="host", platform_id=platform.id
        )
        with pytest.raises(ProtocolValidationError, match="PLATFORM_ASSET_TYPE_MISMATCH"):
            await validate_asset_protocol_binding(
                session, asset_type="database", platform_id=platform.id
            )


@pytest.mark.asyncio
async def test_protocol_api_lists_catalog(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user()
        listed = client.get("/api/v1/protocols/")
        assert listed.status_code == 200
        assert listed.json()["total"] == len(PROTOCOL_CATALOG)
        by_type = client.get("/api/v1/protocols/by-asset-type/database")
        assert by_type.status_code == 200
        assert all(item["category"] == "database" for item in by_type.json()["items"])


@pytest.mark.asyncio
async def test_create_asset_validates_platform_asset_type(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ensure_builtin_protocols(session)
        platform = Platform(name="Linux", category="host", asset_type="host", protocols='["ssh"]')
        session.add(platform)
        await session.commit()
        await session.refresh(platform)
        platform_id = platform.id

    install_db(session_factory)
    with TestClient(app) as client:
        install_user()
        ok = client.post(
            "/api/v1/assets/",
            json={
                "name": "web-1",
                "address": "203.0.113.10",
                "platform_id": platform_id,
                "asset_type": "host",
                "port": 22,
            },
        )
        assert ok.status_code == 200
        assert ok.json()["asset_type"] == "host"

        bad = client.post(
            "/api/v1/assets/",
            json={
                "name": "db-1",
                "address": "203.0.113.11",
                "platform_id": platform_id,
                "asset_type": "database",
                "port": 3306,
            },
        )
        assert bad.status_code == 400
        assert bad.json()["detail"] == "PLATFORM_ASSET_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_sync_platform_protocols_from_json(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ensure_builtin_protocols(session)
        platform = Platform(
            name="DB",
            category="database",
            asset_type="database",
            protocols='["mysql","postgresql"]',
        )
        session.add(platform)
        await session.commit()
        await session.refresh(platform)
        await sync_platform_protocols(session, platform)

    install_db(session_factory)
    with TestClient(app) as client:
        install_user()
        response = client.get(f"/api/v1/assets/platforms/{platform.id}/protocols")
        assert response.status_code == 200
        body = response.json()
        assert body["asset_type"] == "database"
        assert body["total"] == 2
        assert {item["protocol_id"] for item in body["items"]} == {"mysql", "postgresql"}
