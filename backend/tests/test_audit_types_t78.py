"""#t78 分类审计与文件传输入库。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.audits.schemas import FileTransferIngest
from app.api.sessions.service import SessionStatus
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.connector import Connector
from app.models.session import SessionModel


def _audit_user(*, tenant_id: str = "tenant-a") -> dict:
    return {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "permissions": ["audit:write", "audit:read", "connectors:write", "admin"],
    }


def _read_only_user(*, tenant_id: str = "tenant-a") -> dict:
    return {
        "id": "user-2",
        "username": "bob",
        "tenant_id": tenant_id,
        "permissions": ["audit:read"],
    }


def install_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


async def _create_connector(session_factory: async_sessionmaker[AsyncSession], *, tenant_id: str) -> int:
    async with session_factory() as session:
        connector = Connector(
            tenant_id=tenant_id,
            name="edge-1",
            environment="test",
            public_key_fingerprint="sha256:conn",
            status="active",
        )
        session.add(connector)
        await session.commit()
        await session.refresh(connector)
        return connector.id


async def test_file_transfer_ingest_joins_hash_chain_and_redacts_secrets(audit_db) -> None:
    install_db(audit_db)
    connector_id = await _create_connector(audit_db, tenant_id="tenant-a")
    app.dependency_overrides[current_user] = lambda: _audit_user()
    client = TestClient(app)

    first = client.post(
        f"/api/v1/connectors/{connector_id}/file-transfers",
        json={
            "session_id": "sess-1",
            "asset_id": "asset-1",
            "account_id": "root",
            "remote_path": "/var/tmp/id_rsa",
            "direction": "upload",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "status": "success",
            "metadata_password": "should-not-exist",
        },
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["category"] == "file_transfer"
    assert body["sequence_number"] == 1
    assert body["previous_event_hash"] is None
    assert body["event_hash"]
    assert body["metadata"]["remote_path"] == "/var/tmp/id_rsa"
    assert body["metadata"]["sha256"] == "a" * 64
    assert "password" not in body["metadata"]

    second = client.post(
        f"/api/v1/connectors/{connector_id}/file-transfers",
        json={
            "session_id": "sess-1",
            "asset_id": "asset-1",
            "account_id": "root",
            "remote_path": "/etc/shadow",
            "direction": "download",
            "size_bytes": 0,
            "sha256": "",
            "status": "failed",
            "error_code": "SFTPError",
        },
    )
    assert second.status_code == 201
    chained = second.json()
    assert chained["sequence_number"] == 2
    assert chained["previous_event_hash"] == body["event_hash"]
    assert chained["severity"] == "medium"

    listed = client.get("/api/v1/audits/file-transfers")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert {item["id"] for item in listed.json()["items"]} == {body["id"], chained["id"]}

    generic = client.get("/api/v1/audits/events?category=file_transfer")
    assert generic.json()["total"] == 2


async def test_file_transfer_ingest_isolates_tenant_and_inactive_connector(audit_db) -> None:
    install_db(audit_db)
    connector_id = await _create_connector(audit_db, tenant_id="tenant-a")
    async with audit_db() as session:
        other = Connector(
            tenant_id="tenant-b",
            name="other",
            environment="test",
            public_key_fingerprint="sha256:other",
            status="active",
        )
        inactive = Connector(
            tenant_id="tenant-a",
            name="dead",
            environment="test",
            public_key_fingerprint="sha256:dead",
            status="inactive",
        )
        session.add_all([other, inactive])
        await session.commit()
        await session.refresh(other)
        await session.refresh(inactive)
        other_id = other.id
        inactive_id = inactive.id

    payload = {
        "asset_id": "asset-1",
        "account_id": "root",
        "remote_path": "/tmp/a",
        "direction": "upload",
        "size_bytes": 1,
        "status": "success",
        "sha256": "b" * 64,
    }
    app.dependency_overrides[current_user] = lambda: _audit_user(tenant_id="tenant-a")
    client = TestClient(app)
    missing = client.post(f"/api/v1/connectors/{other_id}/file-transfers", json=payload)
    assert missing.status_code == 404
    dead = client.post(f"/api/v1/connectors/{inactive_id}/file-transfers", json=payload)
    assert dead.status_code == 404
    ok = client.post(f"/api/v1/connectors/{connector_id}/file-transfers", json=payload)
    assert ok.status_code == 201

    app.dependency_overrides[current_user] = lambda: _audit_user(tenant_id="tenant-b")
    foreign = client.get("/api/v1/audits/file-transfers")
    assert foreign.json()["total"] == 0


async def test_operate_and_activity_logs_are_tenant_scoped(audit_db) -> None:
    app.dependency_overrides[current_user] = lambda: _audit_user()
    client = TestClient(app)
    operate = client.post(
        "/api/v1/audits/operate-logs",
        json={
            "event_type": "asset.update",
            "action": "update",
            "resource_type": "asset",
            "resource_id": "asset-9",
            "metadata": {"token": "secret-token", "field": "name"},
        },
    )
    assert operate.status_code == 201
    assert operate.json()["category"] == "operate"
    assert operate.json()["metadata"]["token"] == "***REDACTED***"
    assert operate.json()["metadata"]["field"] == "name"

    activity = client.post(
        "/api/v1/audits/activity-logs",
        json={
            "event_type": "user.login",
            "action": "login",
            "resource_type": "user",
            "resource_id": "user-1",
        },
    )
    assert activity.status_code == 201
    assert client.get("/api/v1/audits/operate-logs").json()["total"] == 1
    assert client.get("/api/v1/audits/activity-logs").json()["total"] == 1

    app.dependency_overrides[current_user] = lambda: _audit_user(tenant_id="tenant-b")
    assert client.get("/api/v1/audits/operate-logs").json()["total"] == 0

    app.dependency_overrides[current_user] = lambda: _read_only_user()
    forbidden = client.post(
        "/api/v1/audits/operate-logs",
        json={
            "event_type": "asset.update",
            "action": "update",
            "resource_type": "asset",
            "resource_id": "asset-9",
        },
    )
    assert forbidden.status_code == 403


async def test_password_change_route_writes_redacted_log(audit_db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import auth as auth_api
    from app.services.auth import AuthService

    async def _ok(*_args, **_kwargs) -> None:
        return None

    install_db(audit_db)
    monkeypatch.setattr(AuthService, "change_password", _ok)
    app.dependency_overrides[current_user] = lambda: {
        **_audit_user(),
        "id": 1,
        "permissions": ["assets:read"],
    }
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/password/change",
        json={"old_password": "OldPass-123", "new_password": "NewPass-123"},
    )
    assert response.status_code == 200
    listed = client.get("/api/v1/audits/password-changes")
    # 该用户无 audit:read，应 403；换回审计用户读取。
    assert listed.status_code == 403
    app.dependency_overrides[current_user] = lambda: _audit_user()
    listed = client.get("/api/v1/audits/password-changes")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    item = listed.json()["items"][0]
    assert item["category"] == "password_change"
    assert "OldPass-123" not in str(item)
    assert "NewPass-123" not in str(item)
    assert item["metadata"]["status"] == "completed"
    del auth_api


async def test_online_sessions_hide_connection_url_and_other_tenants(audit_db) -> None:
    install_db(audit_db)
    now = datetime.now(UTC)
    async with audit_db() as session:
        session.add_all(
            [
                SessionModel(
                    id="live-a",
                    subject_id="user-1",
                    tenant_id="tenant-a",
                    asset_id="asset-1",
                    account_id="root",
                    protocol="ssh",
                    status=SessionStatus.ACTIVE.value,
                    connection_token_id="tok-live",
                    connection_url="ssh://root:secret@10.0.0.10",
                    client_ip="203.0.113.10",
                    created_at=now,
                    updated_at=now,
                ),
                SessionModel(
                    id="closed-a",
                    subject_id="user-1",
                    tenant_id="tenant-a",
                    asset_id="asset-1",
                    account_id="root",
                    protocol="ssh",
                    status=SessionStatus.CLOSED.value,
                    connection_token_id="tok-closed",
                    connection_url="ssh://closed",
                    created_at=now,
                    updated_at=now,
                ),
                SessionModel(
                    id="live-b",
                    subject_id="user-9",
                    tenant_id="tenant-b",
                    asset_id="asset-9",
                    account_id="root",
                    protocol="ssh",
                    status=SessionStatus.ACTIVE.value,
                    connection_token_id="tok-b",
                    connection_url="ssh://other",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()

    app.dependency_overrides[current_user] = lambda: _audit_user()
    client = TestClient(app)
    response = client.get("/api/v1/audits/online-sessions")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == "live-a"
    assert item["client_ip"] == "203.0.113.10"
    assert "connection_url" not in item
    assert "connection_token_id" not in item
    assert "secret" not in str(body)


async def test_job_log_omits_stdout_and_joins_chain(audit_db) -> None:
    from app.api.audits.typed import record_job_log

    event = await record_job_log(
        actor=_audit_user(),
        message_id="msg-1",
        job_type="ansible.playbook",
        status="failed",
        metadata={"playbook_name": "ping.yml", "error_code": "TIMEOUT", "stdout": "should-be-dropped-by-caller"},
    )
    assert event.category.value == "job"
    assert event.severity.value == "medium"
    app.dependency_overrides[current_user] = lambda: _audit_user()
    client = TestClient(app)
    listed = client.get("/api/v1/audits/job-logs")
    assert listed.json()["total"] == 1
    item = listed.json()["items"][0]
    assert item["metadata"]["playbook_name"] == "ping.yml"
    assert "stdout" not in item["metadata"]
    assert "password" not in item["metadata"]


def test_file_transfer_ingest_rejects_invalid_sha256() -> None:
    with pytest.raises(ValueError):
        FileTransferIngest(
            asset_id="a",
            account_id="r",
            remote_path="/tmp/x",
            direction="upload",
            size_bytes=1,
            sha256="not-a-hash",
            status="success",
        )


async def test_file_transfer_ingest_requires_connector_write(audit_db) -> None:
    install_db(audit_db)
    connector_id = await _create_connector(audit_db, tenant_id="tenant-a")
    app.dependency_overrides[current_user] = lambda: _read_only_user()
    client = TestClient(app)
    forbidden = client.post(
        f"/api/v1/connectors/{connector_id}/file-transfers",
        json={
            "asset_id": "asset-1",
            "account_id": "root",
            "remote_path": "/tmp/a",
            "direction": "upload",
            "size_bytes": 1,
            "status": "success",
            "sha256": "c" * 64,
        },
    )
    assert forbidden.status_code == 403


async def test_activity_logs_include_auth_and_session_categories(audit_db) -> None:
    from app.api.audits.schemas import AuditCategory, AuditEventCreate
    from app.api.audits.service import audit_service

    actor = _audit_user()
    await audit_service.create_event(
        AuditEventCreate(
            event_type="auth.login",
            category=AuditCategory.auth,
            action="login",
            resource_type="user",
            resource_id="user-1",
        ),
        actor,
    )
    await audit_service.create_event(
        AuditEventCreate(
            event_type="session.opened",
            category=AuditCategory.session,
            action="open",
            resource_type="session",
            resource_id="sess-1",
        ),
        actor,
    )
    app.dependency_overrides[current_user] = lambda: actor
    client = TestClient(app)
    listed = client.get("/api/v1/audits/activity-logs")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert {item["category"] for item in listed.json()["items"]} == {"auth", "session"}


async def test_password_change_helper_drops_secret_ids(audit_db) -> None:
    from app.api.audits.typed import record_password_change

    event = await record_password_change(
        actor=_audit_user(),
        resource_type="account",
        resource_id="1",
        action="credential.rotate",
        status="completed",
        message="账号凭据轮换 completed",
        metadata={
            "rotation_id": 9,
            "secret_id": "sec_should_drop",
            "previous_secret_id": "sec_old",
            "protocol": "ssh",
        },
    )
    assert event.category.value == "password_change"
    assert event.metadata["rotation_id"] == 9
    assert event.metadata["protocol"] == "ssh"
    assert "secret_id" not in event.metadata
    assert "previous_secret_id" not in event.metadata
    assert "sec_should_drop" not in str(event.metadata)
