"""#t64 资产树 / AssetPermission 管理 API。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Asset


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


async def seed_asset(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    asset_id: int,
    tenant_id: str,
    name: str,
    node_id: str | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            Asset(
                id=asset_id,
                name=name,
                address=f"10.0.0.{asset_id}",
                tenant_id=tenant_id,
                platform_id=1,
                node_id=node_id,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_tree_crud_root_guard_and_tenant_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        listed = client.get("/api/v1/asset-nodes/")
        assert listed.status_code == 200
        roots = [item for item in listed.json()["items"] if item["is_root"]]
        assert len(roots) == 1
        root_id = roots[0]["id"]
        assert roots[0]["name"] == "根"

        created = client.post("/api/v1/asset-nodes/", json={"name": "folder"})
        assert created.status_code == 201
        folder_id = created.json()["id"]
        assert created.json()["parent_id"] == root_id
        assert created.json()["is_root"] is False

        hang_root = client.post(
            f"/api/v1/asset-nodes/{root_id}/assets", json={"asset_id": 1}
        )
        perm_root = client.post(
            f"/api/v1/asset-nodes/{root_id}/permissions",
            json={"subject_id": "user-1"},
        )
        delete_root = client.delete(f"/api/v1/asset-nodes/{root_id}")
        assert hang_root.status_code == 400
        assert hang_root.json()["message"] == "根节点只用于组织树，不能挂资产或授权。"
        assert perm_root.status_code == 400
        assert delete_root.status_code == 400

        child = client.post(
            "/api/v1/asset-nodes/", json={"name": "leaf", "parent_id": folder_id}
        )
        assert child.status_code == 201
        delete_folder = client.delete(f"/api/v1/asset-nodes/{folder_id}")
        assert delete_folder.status_code == 400
        assert delete_folder.json()["message"] == "先移走或删除子节点。"

        install_user(tenant_id="tenant-b", permissions=["admin"])
        other_perm = client.get(f"/api/v1/asset-nodes/{folder_id}/permissions")
        other_delete = client.delete(f"/api/v1/asset-nodes/{folder_id}")
        assert other_perm.status_code == 404
        assert other_perm.json()["code"] == "NODE_NOT_FOUND"
        assert other_delete.status_code == 404
        assert "没有权限" not in str(other_perm.json())


@pytest.mark.asyncio
async def test_hang_ungroup_permissions_and_move_impact(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_asset(session_factory, asset_id=10, tenant_id="tenant-a", name="host-10")
    await seed_asset(session_factory, asset_id=11, tenant_id="tenant-a", name="host-11")

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        folder = client.post("/api/v1/asset-nodes/", json={"name": "folder"}).json()
        other = client.post("/api/v1/asset-nodes/", json={"name": "other"}).json()
        folder_id = folder["id"]
        other_id = other["id"]

        hang = client.post(
            f"/api/v1/asset-nodes/{folder_id}/assets", json={"asset_id": 10}
        )
        assert hang.status_code == 200
        assert hang.json()["node_id"] == folder_id

        picker = client.get("/api/v1/asset-nodes/ungrouped-assets")
        labels = {item["id"]: item["location_label"] for item in picker.json()["items"]}
        assert labels[10].startswith("现位于节点")
        assert labels[11] == "未分组"

        granted = client.post(
            f"/api/v1/asset-nodes/{folder_id}/permissions",
            json={
                "subject_id": "ops",
                "subject_type": "user_group",
                "action": "connect",
                "from_ticket": "ticket-64",
            },
        )
        assert granted.status_code == 201
        assert granted.json()["subject_type"] == "user_group"
        assert granted.json()["from_ticket"] == "ticket-64"
        who = client.get(f"/api/v1/asset-nodes/{folder_id}/permissions")
        assert who.json()["items"][0]["inherited"] is False
        asset_who = client.get("/api/v1/asset-permissions/by-asset/10")
        inherited = [row for row in asset_who.json()["items"] if row["inherited"]]
        assert inherited
        assert inherited[0]["inherited_from_node_name"] == "folder"

        impact = client.get(
            "/api/v1/asset-nodes/ungroup-impact", params={"asset_id": 10}
        )
        assert impact.status_code == 200
        assert impact.json()["lost"][0]["subject_id"] == "ops"
        assert impact.json()["lost"][0]["asset_id"] == "10"

        ungrouped = client.post(
            "/api/v1/asset-nodes/ungroup", json={"asset_id": 10}
        )
        assert ungrouped.json()["node_id"] is None
        assert ungrouped.json()["location_label"] == "未分组"
        after = client.get("/api/v1/asset-permissions/by-asset/10")
        assert after.json()["items"] == []

        direct = client.post(
            "/api/v1/asset-permissions/by-asset/10",
            json={"subject_id": "user-1"},
        )
        assert direct.status_code == 201
        still = client.get("/api/v1/asset-permissions/by-asset/10")
        assert any(not row["inherited"] for row in still.json()["items"])

        client.post(f"/api/v1/asset-nodes/{folder_id}/assets", json={"asset_id": 10})
        move_impact = client.get(
            f"/api/v1/asset-nodes/{folder_id}/move-impact",
            params={"parent_id": other_id},
        )
        assert move_impact.status_code == 200
        # still under a node with same perm? moving folder under other keeps folder perm
        # so lost should be empty
        assert move_impact.json()["lost"] == []

        expired = client.post(
            f"/api/v1/asset-nodes/{other_id}/permissions",
            json={
                "subject_id": "user-2",
                "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        )
        assert expired.status_code == 201
        listed = client.get(f"/api/v1/asset-nodes/{other_id}/permissions")
        expired_rows = [row for row in listed.json()["items"] if row["subject_id"] == "user-2"]
        assert expired_rows and expired_rows[0]["expired"] is True

        delete_direct = client.delete(f"/api/v1/asset-permissions/{direct.json()['id']}")
        assert delete_direct.status_code == 204

        # 有资产不能删
        blocked = client.delete(f"/api/v1/asset-nodes/{folder_id}")
        assert blocked.status_code == 400
        assert blocked.json()["message"] == "先把资产移出或移到其他节点。"

        client.post("/api/v1/asset-nodes/ungroup", json={"asset_id": 10})
        gone = client.delete(f"/api/v1/asset-nodes/{folder_id}")
        assert gone.status_code == 204

@pytest.mark.asyncio
async def test_use_surface_lists_only_connectable_assets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_asset(session_factory, asset_id=10, tenant_id="tenant-a", name="allowed")
    await seed_asset(session_factory, asset_id=11, tenant_id="tenant-a", name="hidden")

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin", "assets:read"])
        folder = client.post("/api/v1/asset-nodes/", json={"name": "folder"}).json()
        client.post(f"/api/v1/asset-nodes/{folder['id']}/assets", json={"asset_id": 10})
        client.post(
            f"/api/v1/asset-nodes/{folder['id']}/permissions",
            json={"subject_id": "user-1"},
        )
        listed = client.get("/api/v1/assets/")
        missing = client.get("/api/v1/assets/11")
        assert listed.status_code == 200
        names = [item["name"] for item in listed.json()]
        assert names == ["allowed"]
        assert missing.status_code == 404
        assert missing.json()["message"] == "资产不存在"
        assert "没有权限" not in str(missing.json())
