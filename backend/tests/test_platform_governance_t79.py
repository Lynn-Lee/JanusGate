"""#t79 平台治理：标签、动态配置、偏好、泄露密码库与报表目录。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.audits.schemas import AuditReportSummary
from app.api.governance import router as governance_router
from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.security import password_policy_violations
from app.main import app
from app.models.asset import Asset, Platform
from app.services.auth import AuthService
from app.services.leak_passwords import LEAKED_PASSWORD_REJECTED, leak_password_sha256


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


def install_user(*, tenant_id: str, permissions: list[str], user_id: str = "1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def seed_asset(session_factory: async_sessionmaker[AsyncSession], *, tenant_id: str) -> Asset:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        asset = Asset(id=1, name="prod", address="203.0.113.10", platform_id=1, tenant_id=tenant_id)
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset


@pytest.mark.asyncio
async def test_labels_settings_preferences_and_tenant_isolation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_asset(session_factory, tenant_id="tenant-a")
    install_user(tenant_id="tenant-a", permissions=["admin", "assets:read", "assets:write"])

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/governance/labels/",
            json={"name": "生产", "color": "#ef4444"},
        )
        assert created.status_code == 201
        label_id = created.json()["id"]
        bound = client.put(
            f"/api/v1/governance/labels/{label_id}/assets",
            json={"asset_ids": [1]},
        )
        assert bound.status_code == 200
        assert bound.json()["asset_ids"] == [1]

        listed = client.get("/api/v1/governance/labels/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["name"] == "生产"

        settings = client.get("/api/v1/governance/settings")
        assert settings.status_code == 200
        keys = {item["key"] for item in settings.json()["items"]}
        assert keys == {
            "session_idle_timeout_minutes",
            "password_min_length",
            "weak_password_check_enabled",
            "ui_timezone",
        }
        updated = client.put(
            "/api/v1/governance/settings",
            json={"items": [{"key": "password_min_length", "value": 12}]},
        )
        assert updated.status_code == 200
        by_key = {item["key"]: item["value"] for item in updated.json()["items"]}
        assert by_key["password_min_length"] == 12
        forbidden = client.put(
            "/api/v1/governance/settings",
            json={"items": [{"key": "smtp_password", "value": "secret"}]},
        )
        assert forbidden.status_code == 400
        revisions = client.get("/api/v1/governance/settings/revisions")
        assert revisions.status_code == 200
        assert revisions.json()["items"][0]["key"] == "password_min_length"
        assert revisions.json()["items"][0]["new_value"] == 12

        prefs = client.put(
            "/api/v1/governance/preferences",
            json={"items": [{"key": "theme", "value": "dark"}, {"key": "page_size", "value": 50}]},
        )
        assert prefs.status_code == 200
        pref_map = {item["key"]: item["value"] for item in prefs.json()["items"]}
        assert pref_map["theme"] == "dark"
        assert pref_map["page_size"] == 50

    install_user(tenant_id="tenant-b", permissions=["admin", "assets:read", "assets:write"], user_id="2")
    with TestClient(app) as client:
        other_labels = client.get("/api/v1/governance/labels/")
        assert other_labels.json()["total"] == 0
        other_settings = client.get("/api/v1/governance/settings")
        by_key = {item["key"]: item["value"] for item in other_settings.json()["items"]}
        assert by_key["password_min_length"] == 8
        other_prefs = client.get("/api/v1/governance/preferences")
        pref_map = {item["key"]: item["value"] for item in other_prefs.json()["items"]}
        assert pref_map["theme"] == "light"


@pytest.mark.asyncio
async def test_leak_password_store_hashes_only_and_blocks_auth(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"])
    custom = "UniqueLeak9!"

    with TestClient(app) as client:
        added = client.post("/api/v1/governance/leak-passwords", json={"password": custom})
        assert added.status_code == 201
        body = added.json()
        assert body["sha256"] == leak_password_sha256(custom)
        assert custom not in str(body)
        listed = client.get("/api/v1/governance/leak-passwords")
        assert listed.status_code == 200
        assert listed.json()["builtin_count"] == 5
        assert custom not in listed.text
        builtin = client.post("/api/v1/governance/leak-passwords/check", json={"password": "Password1!"})
        assert builtin.json()["leaked"] is True
        custom_hit = client.post("/api/v1/governance/leak-passwords/check", json={"password": custom})
        assert custom_hit.json()["leaked"] is True
        miss = client.post("/api/v1/governance/leak-passwords/check", json={"password": "NotLeaked9!"})
        assert miss.json()["leaked"] is False

    async with session_factory() as session:
        with pytest.raises(ValueError, match=LEAKED_PASSWORD_REJECTED):
            await AuthService.create_user(session, "bob", "Password1!", tenant_id="tenant-a")
        with pytest.raises(ValueError, match=LEAKED_PASSWORD_REJECTED):
            await AuthService.create_user(session, "carol", custom, tenant_id="tenant-a")
        created = await AuthService.create_user(session, "dave", "NotLeaked9!", tenant_id="tenant-a")
        assert created.username == "dave"


@pytest.mark.asyncio
async def test_password_min_length_overlay_and_policy_helper() -> None:
    assert password_policy_violations("Short1!", min_length=12)
    assert password_policy_violations("Stronger-Password-123") == []


@pytest.mark.asyncio
async def test_report_catalog_run_and_permissions(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["audit:read", "audit:write"])

    async def fake_summary(*, tenant_id: str) -> AuditReportSummary:
        return AuditReportSummary(
            tenant_id=tenant_id,
            total=2,
            high_or_critical_total=0,
            by_severity={"low": 2},
            by_category={"session": 2},
            by_siem_delivery_status={"delivered": 2},
        )

    class FakeCompliance:
        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            del mode
            return {
                "template": "soc2-access",
                "event_ids": ["a1"],
                "message": "should-not-leak",
            }

    async def fake_compliance(*, tenant_id: str, template: str) -> FakeCompliance:
        del tenant_id, template
        return FakeCompliance()

    monkeypatch.setattr("app.api.governance.audit_service.report_summary", fake_summary)
    monkeypatch.setattr("app.api.governance.audit_service.compliance_report", fake_compliance)

    with TestClient(app) as client:
        catalog = client.get("/api/v1/governance/reports")
        assert catalog.status_code == 200
        keys = {item["template_key"] for item in catalog.json()["items"]}
        assert "audit-summary" in keys
        assert "soc2-access" in keys
        saved = client.post(
            "/api/v1/governance/reports",
            json={"name": "月报", "template_key": "audit-summary", "description": "聚合"},
        )
        assert saved.status_code == 201
        ran = client.post("/api/v1/governance/reports/run", json={"template_key": "audit-summary"})
        assert ran.status_code == 200
        assert ran.json()["result"]["total"] == 2
        soc2 = client.post("/api/v1/governance/reports/run", json={"template_key": "soc2-access"})
        assert soc2.status_code == 200
        assert "message" not in soc2.json()["result"]
        unknown = client.post("/api/v1/governance/reports/run", json={"template_key": "payroll"})
        assert unknown.status_code == 400

    install_user(tenant_id="tenant-a", permissions=["assets:read"], user_id="3")
    with TestClient(app) as client:
        denied = client.get("/api/v1/governance/reports")
        assert denied.status_code == 403
        denied_settings = client.get("/api/v1/governance/settings")
        assert denied_settings.status_code == 403


def test_governance_router_get_paths_are_stable() -> None:
    paths = sorted(
        route.path
        for route in governance_router.routes
        if getattr(route, "methods", None) and "GET" in route.methods
    )
    assert paths == [
        "/governance/labels/",
        "/governance/leak-passwords",
        "/governance/preferences",
        "/governance/reports",
        "/governance/settings",
        "/governance/settings/revisions",
    ]
