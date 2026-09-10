"""Phase 6 #t78 分类审计、文件传输入库、会话共享、端点路由与存储后端。"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.ssh_sftp import FileTransferDirection, FileTransferEvent, FileTransferStatus
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.session import SessionModel
from app.services.session_storage import SessionObjectStore, validate_storage_config
from app.services.typed_audit import HashChainFileTransferSink


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


def install_user(*, user_id: str = "user-1", permissions: list[str] | None = None) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": "tenant-a",
        "permissions": permissions or ["audit:write", "audit:read", "sessions:connect", "admin"],
    }


async def test_typed_and_ftp_logs_join_hash_chain(audit_db: async_sessionmaker[AsyncSession]) -> None:
    install_user()
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/audits/typed",
            json={
                "log_kind": "operate",
                "action": "update",
                "resource_type": "asset",
                "resource_id": "1",
                "message": "renamed host",
                "metadata": {"password": "secret-token"},
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["event_type"] == "operate.action"
        assert created.json()["category"] == "operate"
        assert created.json()["metadata"]["password"] == "***REDACTED***"
        assert created.json()["event_hash"]
        assert created.json()["sequence_number"] == 1

        ftp = client.post(
            "/api/v1/audits/ftp-logs",
            json={
                "session_id": "sess-1",
                "asset_id": "1",
                "remote_path": "/tmp/a.bin",
                "direction": "upload",
                "size_bytes": 12,
                "sha256": "abc",
                "status": "success",
            },
        )
        assert ftp.status_code == 201
        assert ftp.json()["event_type"] == "ftp.transfer"
        assert ftp.json()["previous_event_hash"] == created.json()["event_hash"]
        assert ftp.json()["sequence_number"] == 2

        listed = client.get("/api/v1/audits/typed/ftp")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["event_type"] == "ftp.transfer"


@pytest.mark.asyncio
async def test_file_transfer_sink_writes_ftp_audit(
    audit_db: async_sessionmaker[AsyncSession],
) -> None:
    sink = HashChainFileTransferSink(
        actor={
            "id": "user-1",
            "username": "alice",
            "tenant_id": "tenant-a",
            "permissions": ["audit:write"],
        },
        session_id="sess-sftp",
        asset_id="asset-1",
    )
    await sink.emit(
        FileTransferEvent(
            remote_path="/var/log/app.log",
            direction=FileTransferDirection.DOWNLOAD,
            size_bytes=8,
            sha256="deadbeef",
            status=FileTransferStatus.FAILED,
            error_code="SFTP_READ_FAILED",
        )
    )
    install_user()
    with TestClient(app) as client:
        listed = client.get("/api/v1/audits/typed/ftp")
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["metadata"]["status"] == "failed"
        assert listed.json()["items"][0]["session_id"] == "sess-sftp"


async def test_session_share_join_and_endpoint_storage(
    audit_db: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SESSION_OBJECT_STORAGE_ROOT", str(tmp_path))
    install_db(audit_db)
    install_user(user_id="user-1")
    async with audit_db() as session:
        session.add(
            SessionModel(
                id="sess-live",
                subject_id="user-1",
                tenant_id="tenant-a",
                asset_id="1",
                account_id="root",
                protocol="ssh",
                status="active",
            )
        )
        await session.commit()

    with TestClient(app) as client:
        shared = client.post(
            "/api/v1/sessions/sess-live/shares",
            json={"guest_user_id": "guest-2", "mode": "watch"},
        )
        assert shared.status_code == 201, shared.text
        share_id = shared.json()["id"]
        listed_shares = client.get("/api/v1/sessions/sess-live/shares")
        assert listed_shares.json()["total"] == 1

        install_user(user_id="guest-2")
        joined = client.post(f"/api/v1/sessions/sess-live/shares/{share_id}/join")
        assert joined.status_code == 200
        assert joined.json()["status"] == "joined"

        install_user(user_id="user-1")
        endpoint = client.post(
            "/api/v1/session-ops/endpoints",
            json={"name": "ssh-gw", "host": "gw.example.com", "port": 2222, "protocol": "ssh"},
        )
        assert endpoint.status_code == 201
        endpoint_id = endpoint.json()["id"]
        rule = client.post(
            "/api/v1/session-ops/endpoint-rules",
            json={
                "name": "prod-ssh",
                "match_protocol": "ssh",
                "match_host_suffix": "example.com",
                "endpoint_id": endpoint_id,
                "priority": 10,
            },
        )
        assert rule.status_code == 201
        resolved = client.get(
            "/api/v1/session-ops/endpoint-rules/resolve?protocol=ssh&host=db.example.com"
        )
        assert resolved.status_code == 200
        assert resolved.json()["host"] == "gw.example.com"

        secret = client.post(
            "/api/v1/session-ops/storage-backends",
            json={"name": "bad", "kind": "s3", "purpose": "command", "config": {"bucket": "b", "password": "x"}},
        )
        assert secret.status_code == 400

        backend = client.post(
            "/api/v1/session-ops/storage-backends",
            json={"name": "es-cmd", "kind": "es", "purpose": "command", "config": {"index": "commands"}},
        )
        assert backend.status_code == 201, backend.text
        backend_id = backend.json()["id"]
        stored = client.post(
            f"/api/v1/session-ops/storage-backends/{backend_id}/objects",
            json={"object_id": "cmd-1", "command": {"input": "ls /opt"}},
        )
        assert stored.status_code == 201
        search = client.get(f"/api/v1/session-ops/storage-backends/{backend_id}/commands?q=ls")
        assert search.json()["items"] == ["cmd-1"]

        shares_audit = client.get("/api/v1/audits/typed/session-shares")
        assert shares_audit.status_code == 200
        assert shares_audit.json()["total"] >= 1


def test_storage_config_requires_bucket() -> None:
    with pytest.raises(ValueError, match="STORAGE_BUCKET_REQUIRED"):
        validate_storage_config(kind="s3", purpose="replay", config={})
    store = SessionObjectStore(root="/tmp/janusgate-t78-store")
    uri = store.put(
        tenant_id="t",
        kind="local",
        purpose="replay",
        object_id="r1",
        payload=b"asciicast",
        config={},
    )
    assert store.get(tenant_id="t", kind="local", purpose="replay", object_id="r1", config={}) == b"asciicast"
    assert "replay" in uri
