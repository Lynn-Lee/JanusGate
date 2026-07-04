"""Phase 4 automation scheduling API contract tests."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform


class RecordingRedisStream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], int | None]] = []

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self.calls.append((name, fields, maxlen))
        return "1700000000000-0"


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


async def seed_account(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(Asset(id=1, name="prod-linux", address="203.0.113.10", platform_id=1))
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
            )
        )
        await session.commit()


def test_asset_scan_scheduling_api_enqueues_tenant_scoped_job() -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:write"])
        response = client.post(
            "/api/v1/automation/jobs/asset-scans",
            json={"asset_id": 42, "scan_profile": "ssh-baseline"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "1700000000000-0",
        "job_type": "asset.scan",
        "status": "queued",
    }
    assert len(stream.calls) == 1
    stream_name, fields, maxlen = stream.calls[0]
    assert stream_name == "janusgate:automation:jobs"
    assert maxlen == 10_000
    assert fields["tenant_id"] == "tenant-a"
    assert fields["job_type"] == "asset.scan"
    assert fields["requested_by"] == "user-1"
    assert json.loads(fields["payload_json"]) == {
        "asset_id": 42,
        "scan_profile": "ssh-baseline",
    }
    assert "secret" not in json.dumps(fields).lower()


def test_asset_scan_scheduling_api_requires_automation_write_permission() -> None:
    app.dependency_overrides[get_redis] = lambda: RecordingRedisStream()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:read"])
        response = client.post(
            "/api/v1/automation/jobs/asset-scans",
            json={"asset_id": 42, "scan_profile": "ssh-baseline"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 403


def test_playbook_scheduling_api_enqueues_secret_free_job() -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:write"])
        response = client.post(
            "/api/v1/automation/jobs/playbooks",
            json={
                "playbook_name": "linux-baseline.yml",
                "target_asset_ids": [42, 43],
                "check_mode": True,
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "1700000000000-0",
        "job_type": "ansible.playbook",
        "status": "queued",
    }
    assert len(stream.calls) == 1
    _, fields, _ = stream.calls[0]
    assert fields["tenant_id"] == "tenant-a"
    assert fields["job_type"] == "ansible.playbook"
    assert fields["requested_by"] == "user-1"
    assert json.loads(fields["payload_json"]) == {
        "check_mode": True,
        "playbook_name": "linux-baseline.yml",
        "target_asset_ids": [42, 43],
    }
    assert "secret" not in json.dumps(fields).lower()


def test_playbook_scheduling_api_rejects_extra_sensitive_fields() -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:write"])
        response = client.post(
            "/api/v1/automation/jobs/playbooks",
            json={
                "playbook_name": "linux-baseline.yml",
                "target_asset_ids": [42],
                "extra_vars": {"password": "should-not-enter-queue"},
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert stream.calls == []


@pytest.mark.asyncio
async def test_credential_rotation_job_api_enqueues_scoped_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_account(session_factory)
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:write"])
        response = client.post(
            "/api/v1/automation/jobs/credential-rotations",
            json={"account_id": 1, "reason": "quarterly rotation"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "1700000000000-0",
        "job_type": "credential.rotate",
        "status": "queued",
    }
    assert len(stream.calls) == 1
    _, fields, _ = stream.calls[0]
    assert fields["tenant_id"] == "tenant-a"
    assert fields["job_type"] == "credential.rotate"
    assert fields["requested_by"] == "user-1"
    assert json.loads(fields["payload_json"]) == {
        "account_id": 1,
        "reason": "quarterly rotation",
    }
    assert "secret" not in json.dumps(fields).lower()


@pytest.mark.asyncio
async def test_credential_rotation_job_api_rejects_cross_tenant_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_account(session_factory)
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream

    with TestClient(app) as client:
        install_user(tenant_id="tenant-b", permissions=["automation:write"])
        response = client.post(
            "/api/v1/automation/jobs/credential-rotations",
            json={"account_id": 1, "reason": "should not cross tenant"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["code"] == "ACCOUNT_NOT_FOUND"
    assert stream.calls == []
