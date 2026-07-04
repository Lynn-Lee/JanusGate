"""Phase 4 session recording and command search API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
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
async def test_session_recording_api_creates_command_events_and_searches_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        assert recording_response.status_code == 201
        recording = recording_response.json()
        command_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "sudo systemctl restart nginx",
                "exit_code": 0,
                "output_excerpt": "token=raw-secret should be redacted",
            },
        )
        tenant_a_search = client.get("/api/v1/session-recordings/commands?query=nginx")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_search = client.get("/api/v1/session-recordings/commands?query=nginx")
        tenant_b_append = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 2,
                "command": "cat /etc/passwd",
                "exit_code": 0,
            },
        )

    assert recording["tenant_id"] == "tenant-a"
    assert recording["session_id"] == "session-a"
    assert recording["status"] == "recording"
    assert recording["storage_uri"] == "s3://janusgate-recordings/tenant-a/session-a.cast"

    assert command_response.status_code == 201
    command = command_response.json()
    assert command["recording_id"] == recording["id"]
    assert command["command"] == "sudo systemctl restart nginx"
    assert "raw-secret" not in command["output_excerpt"]

    assert tenant_a_search.status_code == 200
    assert tenant_a_search.json()["total"] == 1
    assert tenant_a_search.json()["items"][0]["session_id"] == "session-a"
    assert tenant_a_search.json()["items"][0]["command"] == "sudo systemctl restart nginx"

    assert tenant_b_search.status_code == 200
    assert tenant_b_search.json() == {"items": [], "total": 0}
    assert tenant_b_append.status_code == 404
    assert tenant_b_append.json()["code"] == "SESSION_RECORDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_recording_api_closes_recordings_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        recording = recording_response.json()
        close_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/close"
        )
        second_close_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/close"
        )

        tenant_a_second_recording_response = client.post(
            "/api/v1/sessions/session-b/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-b.cast",
            },
        )
        tenant_a_second_recording = tenant_a_second_recording_response.json()

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_close_response = client.post(
            f"/api/v1/session-recordings/{tenant_a_second_recording['id']}/close"
        )

    assert close_response.status_code == 200
    closed_recording = close_response.json()
    assert closed_recording["status"] == "closed"
    assert closed_recording["ended_at"] is not None

    assert second_close_response.status_code == 404
    assert second_close_response.json()["code"] == "SESSION_RECORDING_NOT_FOUND"

    assert tenant_b_close_response.status_code == 404
    assert tenant_b_close_response.json()["code"] == "SESSION_RECORDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_recording_api_lists_recording_commands_for_playback_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        recording = recording_response.json()
        client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={"sequence": 2, "command": "tail -f /var/log/syslog"},
        )
        client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={"sequence": 1, "command": "whoami"},
        )

        tenant_a_timeline = client.get(
            f"/api/v1/session-recordings/{recording['id']}/commands"
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_timeline = client.get(
            f"/api/v1/session-recordings/{recording['id']}/commands"
        )

    assert tenant_a_timeline.status_code == 200
    assert tenant_a_timeline.json()["total"] == 2
    assert [item["sequence"] for item in tenant_a_timeline.json()["items"]] == [1, 2]
    assert [item["command"] for item in tenant_a_timeline.json()["items"]] == [
        "whoami",
        "tail -f /var/log/syslog",
    ]

    assert tenant_b_timeline.status_code == 404
    assert tenant_b_timeline.json()["code"] == "SESSION_RECORDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_connector_session_recording_ingest_requires_active_same_tenant_connector(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        active_connector_response = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-a",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-a",
                "capabilities": ["ssh"],
            },
        )
        inactive_connector_response = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-inactive",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-inactive",
                "capabilities": ["ssh"],
                "status": "inactive",
            },
        )
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        active_connector = active_connector_response.json()
        inactive_connector = inactive_connector_response.json()
        recording = recording_response.json()

        ingest_response = client.post(
            f"/api/v1/connectors/{active_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "sudo systemctl restart nginx",
                "exit_code": 0,
                "output_excerpt": "password=raw-secret",
            },
        )
        inactive_ingest_response = client.post(
            f"/api/v1/connectors/{inactive_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 2, "command": "whoami"},
        )
        close_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/close"
        )
        closed_ingest_response = client.post(
            f"/api/v1/connectors/{active_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 3, "command": "id"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        cross_tenant_ingest_response = client.post(
            f"/api/v1/connectors/{active_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 4, "command": "hostname"},
        )

    assert ingest_response.status_code == 201
    command = ingest_response.json()
    assert command["recording_id"] == recording["id"]
    assert command["session_id"] == "session-a"
    assert command["command"] == "sudo systemctl restart nginx"
    assert "raw-secret" not in command["output_excerpt"]

    assert inactive_ingest_response.status_code == 403
    assert inactive_ingest_response.json()["code"] == "CONNECTOR_NOT_ACTIVE"

    assert close_response.status_code == 200
    assert closed_ingest_response.status_code == 404
    assert closed_ingest_response.json()["code"] == "SESSION_RECORDING_NOT_FOUND"

    assert cross_tenant_ingest_response.status_code == 404
    assert cross_tenant_ingest_response.json()["code"] == "CONNECTOR_NOT_FOUND"
