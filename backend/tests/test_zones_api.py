"""#t67 网域与网关 API 测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Asset, Platform
from app.models.zone import ZoneModel
from app.protocols.repository import ensure_builtin_protocols


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await ensure_builtin_protocols(session)
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    async def _override_db():
        async with session_factory() as session:
            yield session

    admin_user = {
        "id": "admin-1",
        "tenant_id": "tenant-a",
        "permissions": ["admin", "assets:read", "assets:write", "assets:test"],
    }

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_read_db] = _override_db
    app.dependency_overrides[current_user] = lambda: admin_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def _seed_gateway_asset(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="gw-1",
            address="203.0.113.10",
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=22,
            is_active=True,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset.id


@pytest.mark.asyncio
async def test_zone_crud_and_gateway_registration(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    gateway_id = await _seed_gateway_asset(session_factory)

    create_resp = await client.post("/api/v1/zones/", json={"name": "DMZ"})
    assert create_resp.status_code == 201
    zone_id = create_resp.json()["id"]
    assert create_resp.json()["name"] == "DMZ"

    list_resp = await client.get("/api/v1/zones/")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["items"]) == 1

    add_gw = await client.post(
        f"/api/v1/zones/{zone_id}/gateways",
        json={"gateway_asset_id": gateway_id},
    )
    assert add_gw.status_code == 201
    assert add_gw.json()["gateway_asset_id"] == gateway_id

    gateways = await client.get(f"/api/v1/zones/{zone_id}/gateways")
    assert gateways.status_code == 200
    assert len(gateways.json()["items"]) == 1

    probe = await client.post(f"/api/v1/zones/{zone_id}/gateways/{gateway_id}/probe")
    assert probe.status_code == 200
    assert probe.json()["probe_status"] in {"reachable", "unreachable"}

    remove = await client.delete(f"/api/v1/zones/{zone_id}/gateways/{gateway_id}")
    assert remove.status_code == 204

    delete_zone = await client.delete(f"/api/v1/zones/{zone_id}")
    assert delete_zone.status_code == 204


@pytest.mark.asyncio
async def test_delete_zone_with_assets_conflict(
    session_factory: async_sessionmaker[AsyncSession], client: AsyncClient
) -> None:
    async with session_factory() as session:
        platform = Platform(name="Linux2", category="host", protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        zone = ZoneModel(id="zone_test", tenant_id="tenant-a", name="Prod")
        session.add(zone)
        await session.flush()
        session.add(
            Asset(
                name="inner",
                address="10.0.0.5",
                tenant_id="tenant-a",
                platform_id=platform.id,
                zone_id=zone.id,
                port=22,
                is_active=True,
            )
        )
        await session.commit()

    resp = await client.delete("/api/v1/zones/zone_test")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "ZONE_HAS_ASSETS"
