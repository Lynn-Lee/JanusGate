"""Phase 5 #t58 license and edition boundary contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.audits.service import repository as audit_repository
from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.license import (
    HttpLicenseValidationClient,
    build_license_key,
    get_license_summary,
    get_license_summary_from_external_verifier,
)
from app.main import app


def _future_expires_at() -> str:
    """构造相对当前时间的未来到期时间，供走真实时钟的用例使用。

    经 API 断言 ``license_status == "active"`` 的用例无法像单元用例那样注入 ``now``，
    只能使用真实时钟。写死绝对日期会在该日期过后变成时间炸弹——本文件原先的
    ``2026-08-01`` 即在该日之后使两条用例长期失败。返回值格式与 license payload
    中其它到期时间保持一致（UTC、秒精度、``Z`` 后缀）。
    """

    return (datetime.now(UTC) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


def install_user(*, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


def test_license_summary_defaults_to_community_features_without_license() -> None:
    summary = get_license_summary(
        configured_edition="community",
        license_key="",
        signing_secret="",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "not_configured"
    assert summary.effective_edition == "community"
    assert "core_pam" in summary.enabled_features
    assert "license_management" not in summary.enabled_features
    assert "license_management" in summary.disabled_features


def test_enterprise_edition_fails_closed_without_valid_license() -> None:
    summary = get_license_summary(
        configured_edition="enterprise",
        license_key="invalid-license",
        signing_secret="test-signing-secret",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "invalid"
    assert summary.effective_edition == "community"
    assert "license_management" in summary.disabled_features


def test_active_enterprise_license_enables_declared_enterprise_features() -> None:
    license_key = build_license_key(
        payload={
            "edition": "enterprise",
            "features": ["license_management", "admin_console"],
            "expires_at": "2026-08-01T00:00:00Z",
        },
        signing_secret="test-signing-secret",
    )

    summary = get_license_summary(
        configured_edition="enterprise",
        license_key=license_key,
        signing_secret="test-signing-secret",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "active"
    assert summary.effective_edition == "enterprise"
    assert summary.expires_at == "2026-08-01T00:00:00Z"
    assert "license_management" in summary.enabled_features
    assert "admin_console" in summary.enabled_features


def test_offline_public_key_license_verifier_enables_enterprise_features() -> None:
    private_key = Ed25519PrivateKey.generate()
    payload = {
        "edition": "enterprise",
        "features": ["license_management"],
        "expires_at": "2026-08-01T00:00:00Z",
    }
    license_key = build_license_key(
        payload=payload,
        signing_secret="",
        private_key=private_key,
    )
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )

    summary = get_license_summary(
        configured_edition="enterprise",
        license_key=license_key,
        signing_secret="",
        public_key=public_key,
        license_verifier="ed25519",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "active"
    assert summary.effective_edition == "enterprise"
    assert "license_management" in summary.enabled_features


def test_offline_public_key_license_verifier_fails_closed_for_tampered_license() -> None:
    private_key = Ed25519PrivateKey.generate()
    license_key = build_license_key(
        payload={
            "edition": "enterprise",
            "features": ["license_management"],
            "expires_at": "2026-08-01T00:00:00Z",
        },
        signing_secret="",
        private_key=private_key,
    )
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    payload_part, signature_part = license_key.split(".", maxsplit=1)
    tampered_signature = ("A" if signature_part[0] != "A" else "B") + signature_part[1:]
    tampered_license_key = f"{payload_part}.{tampered_signature}"

    summary = get_license_summary(
        configured_edition="enterprise",
        license_key=tampered_license_key,
        signing_secret="",
        public_key=public_key,
        license_verifier="ed25519",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "invalid"
    assert summary.effective_edition == "community"
    assert "license_management" in summary.disabled_features


@pytest.mark.asyncio
async def test_external_license_validation_enables_enterprise_without_local_signing_material() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "edition": "enterprise",
                "features": ["license_management", "admin_console"],
                "expires_at": "2026-08-01T00:00:00Z",
            },
        )

    client = HttpLicenseValidationClient(
        endpoint_url="https://license.example.test/v1/validate",
        bearer_token="service-token",
        transport=httpx.MockTransport(handler),
    )

    summary = await get_license_summary_from_external_verifier(
        configured_edition="enterprise",
        license_key="opaque-commercial-license",
        client=client,
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "active"
    assert summary.effective_edition == "enterprise"
    assert "license_management" in summary.enabled_features
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer service-token"
    assert requests[0].read() == b'{"license_key":"opaque-commercial-license"}'


@pytest.mark.asyncio
async def test_external_license_validation_fails_closed_for_service_errors() -> None:
    client = HttpLicenseValidationClient(
        endpoint_url="https://license.example.test/v1/validate",
        bearer_token="service-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(503, text="unavailable")),
    )

    summary = await get_license_summary_from_external_verifier(
        configured_edition="enterprise",
        license_key="opaque-commercial-license",
        client=client,
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "invalid"
    assert summary.effective_edition == "community"
    assert "license_management" in summary.disabled_features


def test_expired_enterprise_license_fails_closed() -> None:
    license_key = build_license_key(
        payload={
            "edition": "enterprise",
            "features": ["license_management"],
            "expires_at": "2026-07-01T00:00:00Z",
        },
        signing_secret="test-signing-secret",
    )

    summary = get_license_summary(
        configured_edition="enterprise",
        license_key=license_key,
        signing_secret="test-signing-secret",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )

    assert summary.license_status == "expired"
    assert summary.effective_edition == "community"
    assert "license_management" in summary.disabled_features


def test_license_summary_api_requires_admin_and_never_returns_license_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.dependency_overrides.clear()
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(permissions=["assets:read"])
        forbidden = client.get("/api/v1/admin/license-summary")

        install_user(permissions=["admin"])
        response = client.get("/api/v1/admin/license-summary")

    assert forbidden.status_code == 403
    assert response.status_code == 200
    body = response.json()
    assert body["license_status"] == "not_configured"
    assert body["effective_edition"] == "community"
    assert "license_management" not in body["enabled_features"]
    assert "license_key" not in body


def test_admin_can_persist_license_config_without_secret_echo(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.dependency_overrides.clear()
    install_db(session_factory)
    license_key = build_license_key(
        payload={
            "edition": "enterprise",
            "features": ["license_management"],
            "expires_at": _future_expires_at(),
        },
        signing_secret="test-signing-secret",
    )

    with TestClient(app) as client:
        install_user(permissions=["assets:read"])
        forbidden = client.post(
            "/api/v1/admin/license-config",
            json={
                "configured_edition": "enterprise",
                "license_verifier": "hmac",
                "license_key": license_key,
                "license_signing_secret": "test-signing-secret",
            },
        )

        install_user(permissions=["admin"])
        saved = client.post(
            "/api/v1/admin/license-config",
            json={
                "configured_edition": "enterprise",
                "license_verifier": "hmac",
                "license_key": license_key,
                "license_signing_secret": "test-signing-secret",
            },
        )
        summary = client.get("/api/v1/admin/license-summary")

    assert forbidden.status_code == 403
    assert saved.status_code == 200
    saved_body = saved.json()
    assert saved_body["license_status"] == "active"
    assert saved_body["effective_edition"] == "enterprise"
    assert "license_management" in saved_body["enabled_features"]
    assert "license_key" not in saved_body
    assert "license_signing_secret" not in saved_body

    assert summary.status_code == 200
    summary_body = summary.json()
    assert summary_body["license_status"] == "active"
    assert summary_body["effective_edition"] == "enterprise"
    assert "license_key" not in summary_body


def test_license_config_update_writes_redacted_audit_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.dependency_overrides.clear()
    audit_repository.clear()
    install_db(session_factory)
    license_key = build_license_key(
        payload={
            "edition": "enterprise",
            "features": ["license_management"],
            "expires_at": _future_expires_at(),
        },
        signing_secret="test-signing-secret",
    )

    with TestClient(app) as client:
        install_user(permissions=["admin", "audit:read"])
        saved = client.post(
            "/api/v1/admin/license-config",
            json={
                "configured_edition": "enterprise",
                "license_verifier": "hmac",
                "license_key": license_key,
                "license_signing_secret": "test-signing-secret",
            },
        )
        audits = client.get("/api/v1/audits/events?event_type=admin.license_config.updated")

    assert saved.status_code == 200
    assert audits.status_code == 200
    body = audits.json()
    assert body["total"] == 1
    event = body["items"][0]
    assert event["tenant_id"] == "tenant-a"
    assert event["actor_id"] == "user-1"
    assert event["event_type"] == "admin.license_config.updated"
    assert event["category"] == "audit"
    assert event["action"] == "license_config.update"
    assert event["resource_type"] == "license_configuration"
    assert event["resource_id"] == "active"
    assert event["metadata"] == {
        "configured_edition": "enterprise",
        "effective_edition": "enterprise",
        "license_status": "active",
        "license_verifier": "hmac",
        "has_license_key": True,
        "has_signing_material": True,
        "has_public_key": False,
        "enabled_features": ["audit_reports", "core_pam", "license_management", "workflow_jit"],
    }
    assert license_key not in str(event)
    assert "test-signing-secret" not in str(event)
