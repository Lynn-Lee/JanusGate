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
