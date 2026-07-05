"""Phase 4 webhook endpoint management API contract tests."""
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
async def test_webhook_endpoint_api_creates_and_lists_with_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        create_response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "security-siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["session.recording.closed", "audit.event.created"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
        tenant_a_list = client.get("/api/v1/webhook-endpoints/")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/webhook-endpoints/")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["name"] == "security-siem"
    assert created["url"] == "https://siem.example.test/janusgate"
    assert created["event_types"] == ["session.recording.closed", "audit.event.created"]
    assert created["status"] == "active"
    assert created["signing_secret_configured"] is True
    assert "signing_secret" not in created
    assert "secret" not in created
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {"items": [created], "total": 1}
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_webhook_endpoint_api_rejects_plaintext_http_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "plaintext-sink",
                "url": "http://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
                "signing_secret": "super-secret-webhook-key",
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_WEBHOOK_URL"


@pytest.mark.asyncio
async def test_notification_rule_api_creates_and_lists_with_tenant_scoped_webhook(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        endpoint_response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "security-siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["session.recording.closed"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
        endpoint_id = endpoint_response.json()["id"]

        create_response = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "recording-closed-to-siem",
                "event_types": ["session.recording.closed"],
                "webhook_endpoint_id": endpoint_id,
            },
        )
        tenant_a_list = client.get("/api/v1/notification-rules/")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/notification-rules/")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["name"] == "recording-closed-to-siem"
    assert created["event_types"] == ["session.recording.closed"]
    assert created["webhook_endpoint_id"] == endpoint_id
    assert created["webhook_endpoint_name"] == "security-siem"
    assert created["status"] == "active"
    assert "signing_secret" not in created
    assert "secret" not in created
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {"items": [created], "total": 1}
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_notification_rule_api_rejects_cross_tenant_webhook_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        endpoint_response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "security-siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
        endpoint_id = endpoint_response.json()["id"]

        install_user(tenant_id="tenant-b", permissions=["admin"])
        response = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "cross-tenant-rule",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": endpoint_id,
            },
        )

    assert response.status_code == 404
    assert response.json()["code"] == "WEBHOOK_ENDPOINT_NOT_FOUND"


@pytest.mark.asyncio
async def test_notification_delivery_api_enqueues_and_lists_with_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        endpoint_response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "security-siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
        endpoint_id = endpoint_response.json()["id"]
        rule_response = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "audit-created-to-siem",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": endpoint_id,
            },
        )
        rule_id = rule_response.json()["id"]

        enqueue_response = client.post(
            f"/api/v1/notification-rules/{rule_id}/deliveries",
            json={
                "event_type": "audit.event.created",
                "payload": {"audit_event_id": "evt-1", "token": "raw-secret"},
            },
        )
        tenant_a_list = client.get("/api/v1/notification-deliveries/")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_list = client.get("/api/v1/notification-deliveries/")

    assert enqueue_response.status_code == 202
    created = enqueue_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["notification_rule_id"] == rule_id
    assert created["webhook_endpoint_id"] == endpoint_id
    assert created["event_type"] == "audit.event.created"
    assert created["status"] == "pending"
    assert created["attempts"] == 0
    assert created["next_attempt_at"] is not None
    assert "payload" not in created
    assert "token" not in created
    assert "secret" not in created
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {"items": [created], "total": 1}
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_notification_delivery_api_rejects_unmatched_rule_event_type(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        endpoint_response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "security-siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["session.recording.closed"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
        endpoint_id = endpoint_response.json()["id"]
        rule_response = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "recording-closed-to-siem",
                "event_types": ["session.recording.closed"],
                "webhook_endpoint_id": endpoint_id,
            },
        )
        rule_id = rule_response.json()["id"]

        response = client.post(
            f"/api/v1/notification-rules/{rule_id}/deliveries",
            json={
                "event_type": "audit.event.created",
                "payload": {"audit_event_id": "evt-1"},
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "NOTIFICATION_EVENT_NOT_ALLOWED"
