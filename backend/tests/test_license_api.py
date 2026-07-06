"""Phase 5 #t58 license and edition boundary contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.license import build_license_key, get_license_summary
from app.main import app


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
    tampered_license_key = license_key[:-1] + ("A" if license_key[-1] != "A" else "B")

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
            "expires_at": "2026-08-01T00:00:00Z",
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
