"""#t68 K8s 集群管理 API 测试。"""

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
from app.protocols.repository import ensure_builtin_protocols

_FAKE_CA = """-----BEGIN CERTIFICATE-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA1234567890abcdef
-----END CERTIFICATE-----"""


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
        "permissions": ["admin", "assets:read", "assets:write"],
    }

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_read_db] = _override_db
    app.dependency_overrides[current_user] = lambda: admin_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def _seed_cloud_asset(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        platform = Platform(name="K8s", category="cloud", protocols='["k8s"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="prod-k8s",
            address="https://k8s.example",
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="cloud",
            port=443,
            is_active=True,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset.id


@pytest.mark.asyncio
async def test_k8s_cluster_upsert_and_get(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    asset_id = await _seed_cloud_asset(session_factory)

    upsert = await client.put(
        f"/api/v1/k8s/clusters/{asset_id}",
        json={
            "api_server": "https://k8s.example:6443",
            "server_ca_pem": _FAKE_CA,
            "namespaces": ["default", "ops"],
        },
    )
    assert upsert.status_code == 200
    body = upsert.json()
    assert body["asset_id"] == asset_id
    assert body["api_server"] == "https://k8s.example:6443"
    assert body["namespaces"] == ["default", "ops"]
    assert body["has_server_ca"] is True

    get_resp = await client.get(f"/api/v1/k8s/clusters/{asset_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["namespaces"] == ["default", "ops"]


@pytest.mark.asyncio
async def test_k8s_cluster_rejects_non_cloud_asset(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="host-1",
            address="10.0.0.1",
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=22,
            is_active=True,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        host_id = asset.id

    resp = await client.put(
        f"/api/v1/k8s/clusters/{host_id}",
        json={
            "api_server": "https://k8s.example:6443",
            "server_ca_pem": _FAKE_CA,
            "namespaces": ["default"],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "K8S_ASSET_NOT_FOUND"


@pytest.mark.asyncio
async def test_k8s_cluster_rejects_http_api_server(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    asset_id = await _seed_cloud_asset(session_factory)

    resp = await client.put(
        f"/api/v1/k8s/clusters/{asset_id}",
        json={
            "api_server": "http://k8s.example:6443",
            "server_ca_pem": _FAKE_CA,
            "namespaces": ["default"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "K8S_API_SERVER_MUST_BE_HTTPS"
