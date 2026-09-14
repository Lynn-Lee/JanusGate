"""#t79 平台治理：标签、动态配置、偏好、泄露密码库与报表目录。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.security import hash_password, password_policy_violations
from app.main import app
from app.models.asset import Asset, Platform
from app.services.auth import AuthService
from app.services.leak_passwords import (
    BUILTIN_LEAK_SHA256,
    LEAK_LIST_REJECT_MESSAGE,
    leak_password_sha256,
)


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


def install_user(
    *,
    tenant_id: str,
    permissions: list[str],
    user_id: str = "1",
    username: str = "alice",
) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": username,
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


async def seed_assets(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(Asset(id=1, name="prod-a", address="203.0.113.10", platform_id=1, tenant_id="tenant-a"))
        session.add(Asset(id=2, name="prod-b", address="203.0.113.20", platform_id=1, tenant_id="tenant-b"))
        await session.commit()


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_password_policy_tenant_overlay_can_only_raise_min_length() -> None:
    assert password_policy_violations("Aa1!aaaa", min_length=8) == []
    violations = password_policy_violations("Aa1!aaaa", min_length=12)
    assert any("12" in item for item in violations)


@pytest.mark.asyncio
async def test_labels_crud_tenant_isolation_and_cross_tenant_asset_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_assets(session_factory)
    install_user(tenant_id="tenant-a", permissions=["assets:read", "assets:write"])

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/governance/labels/",
            json={"name": "prod", "color": "#112233"},
        )
        assert created.status_code == 201
        label_id = created.json()["id"]
        assert created.json()["asset_ids"] == []

        bound = client.put(
            f"/api/v1/governance/labels/{label_id}/assets",
            json={"asset_ids": [1]},
        )
        assert bound.status_code == 200
        assert bound.json()["asset_ids"] == [1]

        denied = client.put(
            f"/api/v1/governance/labels/{label_id}/assets",
            json={"asset_ids": [2]},
        )
        assert denied.status_code == 404
        assert denied.json()["detail"] == "ASSET_NOT_FOUND"

        listed = client.get("/api/v1/governance/labels/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        install_user(tenant_id="tenant-b", permissions=["assets:read", "assets:write"], user_id="2")
        other = client.get("/api/v1/governance/labels/")
        assert other.json()["total"] == 0
        hidden = client.put(
            f"/api/v1/governance/labels/{label_id}/assets",
            json={"asset_ids": []},
        )
        assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_tenant_settings_reject_secret_keys_and_write_revisions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"])

    with TestClient(app) as client:
        listed = client.get("/api/v1/governance/settings")
        assert listed.status_code == 200
        keys = {item["key"] for item in listed.json()["items"]}
        assert keys == {
            "session_idle_timeout_minutes",
            "password_min_length",
            "weak_password_check_enabled",
            "ui_timezone",
        }

        rejected = client.put(
            "/api/v1/governance/settings",
            json={"items": [{"key": "signing_secret", "value": "super-secret"}]},
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == "SETTING_KEY_NOT_ALLOWED"

        updated = client.put(
            "/api/v1/governance/settings",
            json={"items": [{"key": "password_min_length", "value": 12}]},
        )
        assert updated.status_code == 200
        values = {item["key"]: item["value"] for item in updated.json()["items"]}
        assert values["password_min_length"] == 12
        assert "super-secret" not in str(updated.json())

        revisions = client.get("/api/v1/governance/settings/revisions")
        assert revisions.status_code == 200
        assert revisions.json()["total"] == 1
        assert revisions.json()["items"][0]["key"] == "password_min_length"
        assert revisions.json()["items"][0]["old_value"] == 8
        assert revisions.json()["items"][0]["new_value"] == 12


@pytest.mark.asyncio
async def test_preferences_are_isolated_per_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=[], user_id="1", username="alice")

    with TestClient(app) as client:
        saved = client.put(
            "/api/v1/governance/preferences",
            json={"items": [{"key": "theme", "value": "dark"}]},
        )
        assert saved.status_code == 200
        values = {item["key"]: item["value"] for item in saved.json()["items"]}
        assert values["theme"] == "dark"

        install_user(tenant_id="tenant-a", permissions=[], user_id="2", username="bob")
        other = client.get("/api/v1/governance/preferences")
        other_values = {item["key"]: item["value"] for item in other.json()["items"]}
        assert other_values["theme"] == "light"


@pytest.mark.asyncio
async def test_leak_password_stores_hash_only_and_blocks_auth(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"])
    digest = leak_password_sha256("UniqueLeak9!")

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/governance/leak-passwords",
            json={"password": "UniqueLeak9!"},
        )
        assert created.status_code == 201
        assert created.json()["sha256"] == digest
        assert "UniqueLeak9!" not in str(created.json())

        listed = client.get("/api/v1/governance/leak-passwords")
        assert listed.status_code == 200
        assert listed.json()["builtin_count"] == len(BUILTIN_LEAK_SHA256)
        assert listed.json()["items"][0]["sha256"] == digest
        assert "UniqueLeak9!" not in str(listed.json())

        checked = client.post(
            "/api/v1/governance/leak-passwords/check",
            json={"password": "UniqueLeak9!"},
        )
        assert checked.status_code == 200
        assert checked.json() == {"leaked": True}
        builtin = client.post(
            "/api/v1/governance/leak-passwords/check",
            json={"password": "Password1!"},
        )
        assert builtin.json() == {"leaked": True}

    async with session_factory() as session:
        with pytest.raises(ValueError, match=LEAK_LIST_REJECT_MESSAGE):
            await AuthService.create_user(session, "carol", "UniqueLeak9!", tenant_id="tenant-a")
        created_user = await AuthService.create_user(
            session, "carol", "Stronger-Password-123", tenant_id="tenant-a"
        )
        created_user.password_hash = hash_password("old-password")
        await session.commit()
        with pytest.raises(ValueError, match=LEAK_LIST_REJECT_MESSAGE):
            await AuthService.change_password(session, created_user.id, "old-password", "Welcome1!")


@pytest.mark.asyncio
async def test_reports_catalog_does_not_leak_audit_details(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["audit:read", "audit:write", "admin"])

    with TestClient(app) as client:
        listed = client.get("/api/v1/governance/reports")
        assert listed.status_code == 200
        keys = {item["template_key"] for item in listed.json()["items"]}
        assert "audit-summary" in keys
        assert "soc2-access" in keys

        created = client.post(
            "/api/v1/governance/reports",
            json={"name": "月度合规", "template_key": "soc2-access", "description": "SOC2"},
        )
        assert created.status_code == 201
        report_id = created.json()["id"]

        ran = client.post("/api/v1/governance/reports/run", json={"report_id": report_id})
        assert ran.status_code == 200
        payload = ran.json()
        serialized = str(payload)
        assert payload["template_key"] == "soc2-access"
        assert "metadata" not in payload["result"]
        assert "message" not in payload["result"]
        assert "resource_id" not in payload["result"]
        assert "session_id" not in payload["result"]
        assert "password=" not in serialized.lower()

        unknown = client.post(
            "/api/v1/governance/reports",
            json={"name": "adhoc", "template_key": "custom-sql"},
        )
        assert unknown.status_code == 400
        assert unknown.json()["detail"] == "REPORT_TEMPLATE_UNKNOWN"


@pytest.mark.asyncio
async def test_governance_permissions_fail_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=[], user_id="9")

    with TestClient(app) as client:
        assert client.get("/api/v1/governance/labels/").status_code == 403
        assert client.get("/api/v1/governance/settings").status_code == 403
        assert client.get("/api/v1/governance/leak-passwords").status_code == 403
        assert client.get("/api/v1/governance/reports").status_code == 403
        prefs = client.get("/api/v1/governance/preferences")
        assert prefs.status_code == 200
