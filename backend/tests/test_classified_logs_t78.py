"""Phase 6 #t78 操作日志与改密日志契约测试。"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.classified_logs import persist_operate_log, persist_password_change_log
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
