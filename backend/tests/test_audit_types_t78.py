"""#t78 分类审计进入 hash chain，以及会话共享 / 端点 / 存储后端。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.session import SessionModel
from app.services.session_ops import store_command_document


@pytest.fixture
async def ops_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def install_db(factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


def install_user(*, permissions: list[str], user_id: str = "user-1", tenant_id: str = "tenant-a") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "permissions": permissions,
    }


async def _seed_session(factory: async_sessionmaker[AsyncSession], *, status: str = "active") -> None:
    async with factory() as session:
        session.add(
            SessionModel(
                id="sess-1",
                subject_id="user-1",
                tenant_id="tenant-a",
                asset_id="asset-1",
                account_id="acc-1",
                protocol="ssh",
                status=status,
                created_at=datetime.now(UTC) - timedelta(minutes=1),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


def test_typed_logs_join_hash_chain_and_redact_secrets() -> None:
    install_user(permissions=["audit:read", "audit:write"])
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/audits/typed",
            json={
                "kind": "operate",
                "action": "update",
                "resource_type": "asset",
                "resource_id": "1",
                "metadata": {"password": "should-redact", "visible": "ok"},
            },
        )
        second = client.post(
            "/api/v1/audits/ftp-logs",
            json={
                "session_id": "sess-1",
                "remote_path": "/tmp/a.bin",
                "direction": "upload",
                "size_bytes": 4,
                "sha256": "a" * 64,
                "status": "success",
            },
        )
        job = client.post(
            "/api/v1/audits/job-logs",
            json={
                "job_type": "ansible.playbook",
                "message_id": "1-0",
                "status": "succeeded",
                "reason": "ok",
            },
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert job.status_code == 201
        operate = first.json()
        ftp = second.json()
        assert operate["event_type"] == "operate.update"
        assert operate["metadata"]["log_kind"] == "operate"
        assert operate["metadata"]["password"] == "***REDACTED***"
        assert operate["metadata"]["visible"] == "ok"
        assert ftp["sequence_number"] == operate["sequence_number"] + 1
        assert ftp["previous_event_hash"] == operate["event_hash"]
        assert ftp["event_type"] == "file_transfer.upload"
        listed = client.get("/api/v1/audits/typed", params={"kind": "file_transfer"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == ftp["id"]


def test_password_change_writes_typed_audit_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    install_user(permissions=["audit:read", "audit:write"])

    async def _ok(*_args: object, **_kwargs: object) -> None:
        return None

    async def _db() -> AsyncGenerator[None, None]:
        yield None

    monkeypatch.setattr("app.services.auth.AuthService.change_password", _ok)
    app.dependency_overrides[get_db] = _db
    with TestClient(app) as client:
        changed = client.post(
            "/api/v1/auth/password/change",
            json={"old_password": "OldPass-123", "new_password": "NewPass-123"},
        )
        assert changed.status_code == 200
        events = client.get("/api/v1/audits/typed", params={"kind": "password_change"})
        assert events.status_code == 200
        assert events.json()["total"] == 1
        payload = events.json()["items"][0]
        assert payload["event_type"] == "password_change.updated"
        assert "password" not in payload["metadata"]
        dumped = str(payload)
        assert "OldPass-123" not in dumped
        assert "NewPass-123" not in dumped


@pytest.mark.asyncio
async def test_session_share_join_and_online_sessions(
    ops_db: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_session(ops_db)
    install_db(ops_db)
    install_user(permissions=["sessions:connect", "audit:read"])
    with TestClient(app) as client:
        online = client.get("/api/v1/audits/online-sessions")
        assert online.status_code == 200
        assert online.json()["total"] == 1
        created = client.post("/api/v1/session-ops/sessions/sess-1/shares")
        assert created.status_code == 201
        code = created.json()["code"]
        assert len(code) >= 16
        joined = client.post("/api/v1/session-ops/joins", json={"code": code})
        assert joined.status_code == 201
        assert joined.json()["mode"] == "observe"
        denied = client.post("/api/v1/session-ops/joins", json={"code": "not-a-real-share-code"})
        assert denied.status_code == 400
        joins = client.get("/api/v1/session-ops/sessions/sess-1/joins")
        assert joins.json()["total"] == 1
        chain = client.get("/api/v1/audits/typed", params={"kind": "user_session"})
        types = {item["event_type"] for item in chain.json()["items"]}
        assert "user_session.shared" in types
        assert "user_session.joined" in types


@pytest.mark.asyncio
async def test_endpoint_routing_and_storage_backends(
    ops_db: async_sessionmaker[AsyncSession],
) -> None:
    install_db(ops_db)
    install_user(permissions=["admin", "audit:read", "sessions:connect"])
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/session-ops/endpoints",
            json={"name": "gw-ssh", "host": "gw.internal", "port": 2222, "protocol": "ssh"},
        )
        assert created.status_code == 201
        endpoint_id = created.json()["id"]
        rule = client.post(
            "/api/v1/session-ops/endpoint-rules",
            json={
                "name": "ssh-default",
                "priority": 10,
                "match_protocol": "ssh",
                "match_asset_id": "",
                "endpoint_id": endpoint_id,
            },
        )
        assert rule.status_code == 201
        resolved = client.post(
            "/api/v1/session-ops/endpoints/resolve",
            json={"protocol": "ssh", "asset_id": "asset-9"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["host"] == "gw.internal"
        secret = client.post(
            "/api/v1/session-ops/storage-backends",
            json={
                "name": "bad",
                "kind": "command",
                "provider": "s3",
                "config": {"access_key": "AKIA"},
            },
        )
        assert secret.status_code == 400
        backend = client.post(
            "/api/v1/session-ops/storage-backends",
            json={
                "name": "es-commands",
                "kind": "command",
                "provider": "es",
                "config": {"prefix": "commands", "index": "session-cmd"},
            },
        )
        assert backend.status_code == 201
        backend_id = backend.json()["id"]

    async with ops_db() as session:
        from app.models.session_ops import SessionStorageBackend

        row = await session.get(SessionStorageBackend, backend_id)
        assert row is not None
        store_command_document(
            tenant_id="tenant-a",
            backend=row,
            recording_id="rec-1",
            command="systemctl restart nginx",
            sequence=0,
        )

    with TestClient(app) as client:
        hits = client.get(
            f"/api/v1/session-ops/storage-backends/{backend_id}/commands",
            params={"q": "nginx"},
        )
        assert hits.status_code == 200
        assert hits.json()["total"] == 1
        assert hits.json()["items"][0]["command"] == "systemctl restart nginx"
