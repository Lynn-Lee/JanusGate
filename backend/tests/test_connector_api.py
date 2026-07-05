"""Phase 4 connector persistent management API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
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
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


def install_user(*, tenant_id: str, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.mark.asyncio
async def test_connector_api_creates_and_lists_connectors_with_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        create_response = client.post(
            "/api/v1/connectors/",
            json={
                "name": "koko-prod-1",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-key",
                "mtls_certificate_fingerprint": "sha256:client-cert",
                "capabilities": ["ssh", "database"],
            },
        )
        tenant_a_list = client.get("/api/v1/connectors/")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/connectors/")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["name"] == "koko-prod-1"
    assert created["environment"] == "prod"
    assert created["public_key_fingerprint"] == "sha256:connector-key"
    assert created["status"] == "active"
    assert created["capabilities"] == ["ssh", "database"]
    assert created["mtls_bound"] is True
    assert created["attestation_bound"] is False
    assert "enrollment_token" not in created
    assert "private_key" not in created
    assert "attestation_digest" not in created
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {"items": [created], "total": 1}
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_connector_api_heartbeat_updates_only_visible_active_connector(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post(
            "/api/v1/connectors/",
            json={
                "name": "koko-prod-1",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-key",
                "capabilities": ["ssh"],
            },
        ).json()
        heartbeat_response = client.post(f"/api/v1/connectors/{created['id']}/heartbeat")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_response = client.post(f"/api/v1/connectors/{created['id']}/heartbeat")

    assert heartbeat_response.status_code == 200
    heartbeat = heartbeat_response.json()
    assert heartbeat["id"] == created["id"]
    assert heartbeat["last_heartbeat_at"] is not None
    assert tenant_b_response.status_code == 404
    assert tenant_b_response.json()["code"] == "CONNECTOR_NOT_FOUND"


@pytest.mark.asyncio
async def test_connector_api_rejects_inactive_connector_key_rotation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post(
            "/api/v1/connectors/",
            json={
                "name": "koko-prod-1",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-key",
                "capabilities": ["ssh"],
                "status": "inactive",
            },
        ).json()
        response = client.post(
            f"/api/v1/connectors/{created['id']}/rotate-key",
            json={"public_key_fingerprint": "sha256:new-key"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "CONNECTOR_NOT_ACTIVE"
