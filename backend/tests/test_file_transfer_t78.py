"""Phase 6 #t78 文件传输日志入库与 hash chain 契约测试。"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app

SUCCESS_DIGEST = sha256(b"payload").hexdigest()


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


def _create_recording(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/sessions/session-a/recordings",
        json={
            "asset_id": "asset-1",
            "account_id": "account-1",
            "protocol": "ssh",
            "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
        },
    )
    assert response.status_code == 201
    return response.json()


def _transfer_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "remote_path": "/var/tmp/secret=raw-secret/backup.tgz",
        "direction": "upload",
        "size_bytes": 7,
        "sha256": SUCCESS_DIGEST,
        "status": "success",
        "error_code": "",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_file_transfer_api_persists_redacted_path_and_hash_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
        recording = _create_recording(client)
        created = client.post(
            f"/api/v1/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(),
        )
        listing = client.get(
            f"/api/v1/session-recordings/{recording['id']}/file-transfers"
        )
        tenant_list = client.get("/api/v1/file-transfers/")
        audits = client.get("/api/v1/audits/events?event_type=session.file_transfer")

        install_user(tenant_id="tenant-b", permissions=["admin", "audit:read"])
        cross_recording = client.get(
            f"/api/v1/session-recordings/{recording['id']}/file-transfers"
        )
        cross_tenant = client.get("/api/v1/file-transfers/")

    assert created.status_code == 201
    body = created.json()
    assert body["session_id"] == "session-a"
    assert body["direction"] == "upload"
    assert body["sha256"] == SUCCESS_DIGEST
    assert "raw-secret" not in body["remote_path"]
    assert "secret=[REDACTED]" in body["remote_path"]
    assert body["audit_event_id"]

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == body["id"]
    assert tenant_list.status_code == 200
    assert tenant_list.json()["total"] == 1

    assert audits.status_code == 200
    assert audits.json()["total"] == 1
    event = audits.json()["items"][0]
    assert event["id"] == body["audit_event_id"]
    assert event["event_type"] == "session.file_transfer"
    assert event["action"] == "file.upload"
    assert event["event_hash"]
    assert event["sequence_number"] == 1
    assert event["metadata"]["sha256"] == SUCCESS_DIGEST
    assert "raw-secret" not in str(event["metadata"])

    assert cross_recording.status_code == 404
    assert cross_tenant.status_code == 200
    assert cross_tenant.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_failed_file_transfer_is_visible_without_content(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
        recording = _create_recording(client)
        created = client.post(
            f"/api/v1/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(
                status="failed",
                sha256="",
                error_code="SFTPNoSuchFile",
                size_bytes=0,
                direction="download",
                remote_path="/etc/shadow",
            ),
        )
        audits = client.get("/api/v1/audits/events?event_type=session.file_transfer")

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "failed"
    assert body["sha256"] == ""
    assert body["error_code"] == "SFTPNoSuchFile"
    assert audits.json()["items"][0]["severity"] == "medium"
    assert audits.json()["items"][0]["action"] == "file.download"


@pytest.mark.asyncio
async def test_connector_file_transfer_ingest_fail_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        active = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-a",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-a",
                "capabilities": ["ssh"],
            },
        )
        inactive = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-inactive",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-inactive",
                "capabilities": ["ssh"],
                "status": "inactive",
            },
        )
        recording = _create_recording(client)
        ingest = client.post(
            f"/api/v1/connectors/{active.json()['id']}"
            f"/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(),
        )
        inactive_ingest = client.post(
            f"/api/v1/connectors/{inactive.json()['id']}"
            f"/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(),
        )
        client.post(f"/api/v1/session-recordings/{recording['id']}/close")
        closed = client.post(
            f"/api/v1/connectors/{active.json()['id']}"
            f"/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(),
        )
        install_user(tenant_id="tenant-b", permissions=["admin"])
        cross = client.post(
            f"/api/v1/connectors/{active.json()['id']}"
            f"/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(),
        )

    assert ingest.status_code == 201
    assert inactive_ingest.status_code == 403
    assert inactive_ingest.json()["code"] == "CONNECTOR_NOT_ACTIVE"
    assert closed.status_code == 404
    assert closed.json()["code"] == "SESSION_RECORDING_NOT_FOUND"
    assert cross.status_code == 404
    assert cross.json()["code"] == "CONNECTOR_NOT_FOUND"


@pytest.mark.asyncio
async def test_success_transfer_rejects_invalid_sha256(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording = _create_recording(client)
        response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/file-transfers",
            json=_transfer_payload(sha256="not-a-digest"),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "FILE_TRANSFER_SHA256_INVALID"
