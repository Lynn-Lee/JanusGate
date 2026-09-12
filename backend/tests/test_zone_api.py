"""#t67 网域 CRUD：权限、删除清空 zone_id、host-class 校验。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Asset, Platform
from app.models.zone import Zone


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


async def seed_host(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = "tenant-a",
    name: str = "gw-1",
    asset_type: str = "host",
) -> Asset:
    async with session_factory() as session:
        platform = Platform(name=f"plat-{name}", category="host", asset_type=asset_type, protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name=name,
            address="10.0.0.1",
            tenant_id=tenant_id,
            platform_id=platform.id,
            asset_type=asset_type,
            port=22,
            username="root",
            is_active=True,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset


@pytest.mark.asyncio
async def test_zone_create_edit_list_delete(session_factory: async_sessionmaker[AsyncSession]) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["acl:read", "acl:write"])
    gw = await seed_host(session_factory, name="jump-a")
    target = await seed_host(session_factory, name="target-a")

    with TestClient(app) as client:
        empty = client.get("/api/v1/zones/")
        assert empty.status_code == 200
        assert empty.json()["total"] == 0
        assert "没有权限" not in str(empty.json())

        created = client.post(
            "/api/v1/zones/",
            json={"name": "生产网域", "gateway_asset_ids": [gw.id]},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "生产网域"
        assert body["gateway_count"] == 1
        assert body["gateway_asset_ids"] == [gw.id]
        zone_id = body["id"]

        listed = client.get("/api/v1/zones/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        patched = client.patch(
            f"/api/v1/zones/{zone_id}",
            json={"name": "生产网域-改", "gateway_asset_ids": []},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "生产网域-改"
        assert patched.json()["gateway_count"] == 0

        # attach asset then delete zone → clears zone_id
        async with session_factory() as session:
            asset = await session.get(Asset, target.id)
            assert asset is not None
            asset.zone_id = zone_id
            await session.commit()

        deleted = client.delete(f"/api/v1/zones/{zone_id}")
        assert deleted.status_code == 204

        async with session_factory() as session:
            asset = await session.get(Asset, target.id)
            assert asset is not None
            assert asset.zone_id is None
            assert await session.get(Zone, zone_id) is None


@pytest.mark.asyncio
async def test_zone_delete_with_members_clears_not_cascade_assets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"])
    gw = await seed_host(session_factory, name="jump-b")
    target = await seed_host(session_factory, name="target-b")

    with TestClient(app) as client:
        zone_id = client.post(
            "/api/v1/zones/",
            json={"name": "有成员", "gateway_asset_ids": [gw.id]},
        ).json()["id"]

    async with session_factory() as session:
        asset = await session.get(Asset, target.id)
        assert asset is not None
        asset.zone_id = zone_id
        await session.commit()

    with TestClient(app) as client:
        assert client.delete(f"/api/v1/zones/{zone_id}").status_code == 204

    async with session_factory() as session:
        assert await session.get(Asset, target.id) is not None
        assert await session.get(Asset, gw.id) is not None
        asset = await session.get(Asset, target.id)
        assert asset is not None and asset.zone_id is None


@pytest.mark.asyncio
async def test_zone_rejects_non_host_class_gateway(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["acl:write", "acl:read"])
    db_asset = await seed_host(session_factory, name="mysql-1", asset_type="database")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/zones/",
            json={"name": "坏网关", "gateway_asset_ids": [db_asset.id]},
        )
        assert resp.status_code == 400
        assert "host-class" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_zone_permission_hide_no_没有权限_copy(  # noqa: N802
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=[])

    with TestClient(app) as client:
        denied = client.get("/api/v1/zones/")
        assert denied.status_code == 403
        body = denied.json()
        assert "没有权限" not in str(body)


@pytest.mark.asyncio
async def test_asset_update_zone_id_and_dropdown_list(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["acl:write", "acl:read", "assets:write", "assets:read"])
    gw = await seed_host(session_factory, name="jump-c")
    target = await seed_host(session_factory, name="target-c")

    with TestClient(app) as client:
        zone_id = client.post(
            "/api/v1/zones/",
            json={"name": "可选网域", "gateway_asset_ids": [gw.id]},
        ).json()["id"]
        zones = client.get("/api/v1/zones/")
        assert zones.status_code == 200
        assert any(item["id"] == zone_id for item in zones.json()["items"])

        updated = client.patch(f"/api/v1/assets/{target.id}", json={"zone_id": zone_id})
        assert updated.status_code == 200
        assert updated.json()["zone_id"] == zone_id

        cleared = client.patch(f"/api/v1/assets/{target.id}", json={"zone_id": None})
        assert cleared.status_code == 200
        assert cleared.json()["zone_id"] is None
