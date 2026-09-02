"""#t65 overlay ACL CRUD：租户隔离、权限、字段校验。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.acl import ConnectMethodAclModel, LoginAssetAclModel, OverlayAclAction
from app.models.asset_tree import NODE_RESOURCE, NodeModel
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


def _login_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "block-bob",
        "priority": 10,
        "action": "reject",
        "subject_id": "42",
    }
    payload.update(overrides)
    return payload


def _login_asset_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "night-block",
        "priority": 10,
        "action": "reject",
        "resource_type": "asset",
        "resource_id": "10",
        "ip_cidr": "10.0.0.0/8",
        "time_start": "09:00",
        "time_end": "18:00",
    }
    payload.update(overrides)
    return payload


def _connect_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "no-ssh",
        "priority": 10,
        "action": "reject",
        "protocol": "ssh",
        "resource_type": "asset",
        "resource_id": "10",
    }
    payload.update(overrides)
    return payload


async def seed_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    username: str,
    tenant_id: str,
    is_active: bool = True,
) -> None:
    async with session_factory() as session:
        session.add(
            User(
                id=user_id,
                username=username,
                tenant_id=tenant_id,
                password_hash="x",
                is_active=is_active,
                display_name="",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_login_acl_crud_is_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_user(session_factory, user_id=42, username="bob", tenant_id="tenant-a")
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post("/api/v1/login-acls/", json=_login_payload())
        listed_a = client.get("/api/v1/login-acls/")
        fetched_a = client.get(f"/api/v1/login-acls/{created.json()['id']}")
        patched = client.patch(
            f"/api/v1/login-acls/{created.json()['id']}",
            json={"priority": 5, "action": "accept"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        listed_b = client.get("/api/v1/login-acls/")
        fetched_b = client.get(f"/api/v1/login-acls/{created.json()['id']}")
        patched_b = client.patch(
            f"/api/v1/login-acls/{created.json()['id']}", json={"priority": 99}
        )
        deleted_b = client.delete(f"/api/v1/login-acls/{created.json()['id']}")

        install_user(tenant_id="tenant-a", permissions=["admin"])
        deleted_a = client.delete(f"/api/v1/login-acls/{created.json()['id']}")

    assert created.status_code == 201
    assert created.json()["subject_id"] == "42"
    assert created.json()["subject_username"] == "bob"
    assert created.json()["action"] == "reject"
    assert listed_a.json()["total"] == 1
    assert fetched_a.status_code == 200
    assert patched.json()["priority"] == 5
    assert patched.json()["action"] == "accept"
    assert listed_b.json() == {"items": [], "total": 0}
    assert fetched_b.status_code == 404
    assert fetched_b.json()["code"] == "LOGIN_ACL_NOT_FOUND"
    assert patched_b.status_code == 404
    assert deleted_b.status_code == 404
    assert deleted_a.status_code == 204


@pytest.mark.asyncio
async def test_login_asset_and_connect_method_crud(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["acl:write", "acl:read"])
        asset_acl = client.post("/api/v1/login-asset-acls/", json=_login_asset_payload())
        wrap = client.post(
            "/api/v1/login-asset-acls/",
            json=_login_asset_payload(time_start="22:00", time_end="06:00"),
        )
        bad_cidr = client.post(
            "/api/v1/login-asset-acls/", json=_login_asset_payload(ip_cidr="999.0.0.0/8")
        )
        method = client.post("/api/v1/connect-method-acls/", json=_connect_payload())
        all_assets = client.post(
            "/api/v1/connect-method-acls/",
            json=_connect_payload(resource_type="", resource_id="", protocol="k8s"),
        )
        bad_proto = client.post(
            "/api/v1/connect-method-acls/", json=_connect_payload(protocol="rdp")
        )
        listed_asset = client.get("/api/v1/login-asset-acls/")
        listed_method = client.get("/api/v1/connect-method-acls/")
        deleted_asset = client.delete(f"/api/v1/login-asset-acls/{asset_acl.json()['id']}")
        deleted_method = client.delete(f"/api/v1/connect-method-acls/{method.json()['id']}")

    assert asset_acl.status_code == 201
    assert asset_acl.json()["ip_cidr"] == "10.0.0.0/8"
    assert asset_acl.json()["time_start"] == "09:00"
    assert asset_acl.json()["time_end"] == "18:00"
    assert wrap.status_code == 400
    assert bad_cidr.status_code == 400
    assert method.status_code == 201
    assert all_assets.status_code == 201
    assert all_assets.json()["protocol"] == "k8s"
    assert all_assets.json()["resource_type"] is None
    assert bad_proto.status_code == 400
    assert listed_asset.json()["total"] == 1
    assert listed_method.json()["total"] == 2
    assert deleted_asset.status_code == 204
    assert deleted_method.status_code == 204


@pytest.mark.asyncio
async def test_overlay_acl_requires_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[])
        create_login = client.post("/api/v1/login-acls/", json=_login_payload())
        list_login = client.get("/api/v1/login-acls/")
        create_asset = client.post("/api/v1/login-asset-acls/", json=_login_asset_payload())
        list_asset = client.get("/api/v1/login-asset-acls/")
        create_method = client.post("/api/v1/connect-method-acls/", json=_connect_payload())
        list_method = client.get("/api/v1/connect-method-acls/")

        install_user(tenant_id="tenant-a", permissions=["acl:read"])
        list_ok = client.get("/api/v1/login-acls/")
        write_denied = client.post("/api/v1/login-acls/", json=_login_payload())

    assert create_login.status_code == 403
    assert list_login.status_code == 403
    assert create_asset.status_code == 403
    assert list_asset.status_code == 403
    assert create_method.status_code == 403
    assert list_method.status_code == 403
    assert list_ok.status_code == 200
    assert write_denied.status_code == 403


@pytest.mark.asyncio
async def test_delete_node_cascades_overlay_acls(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as session:
        root = NodeModel(
            id="node-root",
            tenant_id="tenant-a",
            parent_id=None,
            name="根",
            ancestor_ids_json="[]",
        )
        folder = NodeModel(
            id="node-folder",
            tenant_id="tenant-a",
            parent_id="node-root",
            name="folder",
            ancestor_ids_json='["node-root"]',
        )
        session.add_all(
            [
                root,
                folder,
                LoginAssetAclModel(
                    id="laa-node",
                    tenant_id="tenant-a",
                    name="node-block",
                    priority=50,
                    action=OverlayAclAction.REJECT,
                    resource_type=NODE_RESOURCE,
                    resource_id="node-folder",
                ),
                ConnectMethodAclModel(
                    id="cma-node",
                    tenant_id="tenant-a",
                    name="node-ssh",
                    priority=50,
                    action=OverlayAclAction.REJECT,
                    protocol="ssh",
                    resource_type=NODE_RESOURCE,
                    resource_id="node-folder",
                ),
            ]
        )
        await session.commit()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        deleted = client.delete("/api/v1/asset-nodes/node-folder")
        listed_asset = client.get("/api/v1/login-asset-acls/")
        listed_method = client.get("/api/v1/connect-method-acls/")

    assert deleted.status_code == 204
    assert listed_asset.json() == {"items": [], "total": 0}
    assert listed_method.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_login_acl_unknown_subject_returns_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post("/api/v1/login-acls/", json=_login_payload(subject_id="999"))

    assert created.status_code == 404
    assert created.json()["message"] == "用户不存在"
    assert created.json()["detail"] == "用户不存在"


@pytest.mark.asyncio
async def test_users_directory_lists_tenant_usernames(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_user(session_factory, user_id=42, username="bob", tenant_id="tenant-a")
    await seed_user(session_factory, user_id=7, username="carol", tenant_id="tenant-b")
    await seed_user(
        session_factory, user_id=8, username="inactive", tenant_id="tenant-a", is_active=False
    )
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["acl:read"])
        listed_a = client.get("/api/v1/users/")
        install_user(tenant_id="tenant-b", permissions=["acl:read"])
        listed_b = client.get("/api/v1/users/")
        install_user(tenant_id="tenant-a", permissions=[])
        denied = client.get("/api/v1/users/")
        install_user(tenant_id="tenant-a", permissions=["assets:read"])
        listed_assets = client.get("/api/v1/users/")

    assert listed_a.status_code == 200
    assert listed_a.json()["total"] == 1
    assert listed_a.json()["items"] == [{"id": 42, "username": "bob", "display_name": ""}]
    assert "password" not in listed_a.json()["items"][0]
    assert "password_hash" not in listed_a.json()["items"][0]
    assert listed_b.status_code == 200
    assert listed_b.json()["items"] == [{"id": 7, "username": "carol", "display_name": ""}]
    assert denied.status_code == 404
    assert listed_assets.status_code == 200
    assert listed_assets.json()["items"][0]["username"] == "bob"
