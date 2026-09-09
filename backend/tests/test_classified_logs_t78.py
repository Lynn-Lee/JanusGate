"""Phase 6 #t78 操作日志与改密日志契约测试。"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.classified_logs import (
    persist_login_session,
    persist_operate_log,
    persist_password_change_log,
)
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
        "permissions": permissions,
    }


@pytest.mark.asyncio
async def test_operate_log_joins_hash_chain_and_is_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as db:
        await persist_operate_log(
            db=db,
            user={"id": "user-1", "username": "alice", "tenant_id": "tenant-a"},
            resource_type="asset",
            resource_id="42",
            action="create",
            summary="create asset bastion",
        )

    install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        listing = client.get("/api/v1/operate-logs/")
        audits = client.get("/api/v1/audits/events?event_type=admin.operate")

    install_user(tenant_id="tenant-b", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        cross = client.get("/api/v1/operate-logs/")

    install_user(tenant_id="tenant-a", permissions=["assets:read"])
    with TestClient(app) as client:
        denied = client.get("/api/v1/operate-logs/")

    install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        activities = client.get("/api/v1/activity-logs/")

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert item["resource_type"] == "asset"
    assert item["action"] == "create"
    assert item["summary"] == "create asset bastion"
    assert audits.status_code == 200
    assert audits.json()["total"] == 1
    event = audits.json()["items"][0]
    assert event["id"] == item["audit_event_id"]
    assert event["event_hash"]
    assert event["metadata"]["summary"] == "create asset bastion"
    assert activities.status_code == 200
    assert activities.json()["total"] == 1
    assert activities.json()["items"][0]["audit_event_id"] == item["audit_event_id"]
    assert activities.json()["items"][0]["action"] == "create"
    assert cross.status_code == 200
    assert cross.json() == {"items": [], "total": 0}
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_password_change_log_omits_secrets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as db:
        await persist_password_change_log(
            db=db,
            user={"id": "user-1", "username": "alice", "tenant_id": "tenant-a"},
            method="self",
        )

    install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        listing = client.get("/api/v1/password-change-logs/")
        audits = client.get("/api/v1/audits/events?event_type=auth.password_change")

    assert listing.status_code == 200
    body = listing.json()["items"][0]
    assert body["username"] == "alice"
    assert body["method"] == "self"
    dumped = str(listing.json()) + str(audits.json())
    assert "NewPass" not in dumped
    assert "password_hash" not in dumped
    event = audits.json()["items"][0]
    assert event["id"] == body["audit_event_id"]
    assert event["severity"] == "medium"
    assert event["metadata"] == {"method": "self", "username": "alice"}

    install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        activities = client.get("/api/v1/activity-logs/")
    assert activities.json()["total"] == 1
    assert activities.json()["items"][0]["action"] == "password_change"
    assert activities.json()["items"][0]["audit_event_id"] == body["audit_event_id"]


@pytest.mark.asyncio
async def test_login_session_joins_hash_chain_without_tokens(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as db:
        await persist_login_session(
            db=db,
            user={"id": "user-1", "username": "alice", "tenant_id": "tenant-a"},
            client_ip="203.0.113.10",
        )

    install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        listing = client.get("/api/v1/online-sessions/")
        activities = client.get("/api/v1/activity-logs/")
        audits = client.get("/api/v1/audits/events?event_type=auth.login")

    install_user(tenant_id="tenant-b", permissions=["admin", "audit:read"])
    with TestClient(app) as client:
        cross = client.get("/api/v1/online-sessions/")

    install_user(tenant_id="tenant-a", permissions=["assets:read"])
    with TestClient(app) as client:
        denied = client.get("/api/v1/online-sessions/")

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert item["username"] == "alice"
    assert item["client_ip"] == "203.0.113.10"
    assert item["status"] == "active"
    assert item["ended_audit_event_id"] == ""
    dumped = str(listing.json()) + str(audits.json()) + str(activities.json())
    assert "access_token" not in dumped
    assert "refresh_token" not in dumped
    event = audits.json()["items"][0]
    assert event["id"] == item["audit_event_id"]
    assert event["event_hash"]
    assert event["metadata"] == {"username": "alice", "client_ip": "203.0.113.10"}
    assert activities.json()["total"] == 1
    assert activities.json()["items"][0]["action"] == "login"
    assert cross.status_code == 200
    assert cross.json() == {"items": [], "total": 0}
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_end_online_session_is_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    async with session_factory() as db:
        row = await persist_login_session(
            db=db,
            user={"id": "user-1", "username": "alice", "tenant_id": "tenant-a"},
            client_ip="198.51.100.8",
        )
        session_id = row.id

    install_user(tenant_id="tenant-b", permissions=["admin"])
    with TestClient(app) as client:
        missing = client.post(f"/api/v1/online-sessions/{session_id}/end")

    install_user(tenant_id="tenant-a", permissions=["audit:read"])
    with TestClient(app) as client:
        forbidden = client.post(f"/api/v1/online-sessions/{session_id}/end")

    install_user(tenant_id="tenant-a", permissions=["admin"])
    with TestClient(app) as client:
        ended = client.post(f"/api/v1/online-sessions/{session_id}/end")
        again = client.post(f"/api/v1/online-sessions/{session_id}/end")
        audits = client.get("/api/v1/audits/events?event_type=auth.session_end")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ONLINE_SESSION_NOT_FOUND"
    assert forbidden.status_code == 403
    assert ended.status_code == 200
    body = ended.json()
    assert body["status"] == "ended"
    assert body["ended_audit_event_id"]
    assert again.status_code == 200
    assert again.json()["ended_audit_event_id"] == body["ended_audit_event_id"]
    assert audits.json()["total"] == 1
    assert audits.json()["items"][0]["id"] == body["ended_audit_event_id"]
    dumped = str(ended.json()) + str(audits.json())
    assert "access_token" not in dumped
    assert "refresh_token" not in dumped
