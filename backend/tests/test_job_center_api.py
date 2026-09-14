"""#t77 作业中心 API：CRUD、入队、周期 tick、租户隔离。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.job_center import Job


class RecordingRedisStream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], int | None]] = []
        self._counter = 0

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        del approximate
        self.calls.append((name, fields, maxlen))
        self._counter += 1
        return f"170000000000{self._counter}-0"


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


def install_user(*, tenant_id: str, permissions: list[str], user_id: str = "user-1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


def install_queue(stream: RecordingRedisStream) -> None:
    app.dependency_overrides[get_redis] = lambda: stream


async def seed_assets(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                tenant_id="tenant-a",
                name="prod-linux-1",
                address="203.0.113.10",
                platform_id=1,
                is_active=True,
            )
        )
        session.add(
            Asset(
                id=2,
                tenant_id="tenant-b",
                name="other-tenant",
                address="203.0.113.11",
                platform_id=1,
                is_active=True,
            )
        )
        session.add(
            Account(
                id=1,
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
            )
        )
        session.add(
            Account(
                id=2,
                tenant_id="tenant-b",
                asset_id=2,
                username="other",
                protocol="ssh",
                secret_id="sec_other",
            )
        )
        await session.commit()


def _create_playbook(client: TestClient) -> int:
    response = client.post(
        "/api/v1/job-center/playbooks/",
        json={
            "name": "基线",
            "filename": "linux-baseline.yml",
            "content": "---\n- hosts: all\n  tasks: []\n",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


@pytest.mark.asyncio
async def test_job_center_playbook_job_run_enqueues_json_only_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:read", "automation:write"])
    stream = RecordingRedisStream()
    install_queue(stream)
    await seed_assets(session_factory)

    with TestClient(app) as client:
        playbook_id = _create_playbook(client)
        variable = client.post(
            "/api/v1/job-center/variables/",
            json={"name": "region", "extra_vars": {"region": "ap-east"}},
        )
        assert variable.status_code == 201, variable.text
        created = client.post(
            "/api/v1/job-center/jobs/",
            json={
                "name": "基线巡检",
                "kind": "playbook",
                "playbook_id": playbook_id,
                "target_asset_ids": [1],
                "extra_var_names": ["region"],
                "runas_account_id": 1,
                "check_mode": True,
            },
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        run = client.post(f"/api/v1/job-center/jobs/{job_id}/run")
        assert run.status_code == 202, run.text
        assert run.json()["job_type"] == "ansible.playbook"
        assert run.json()["status"] == "queued"

        listed = client.get("/api/v1/job-center/executions/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["job_id"] == job_id

    assert len(stream.calls) == 1
    fields = stream.calls[0][1]
    assert fields["payload_format"] == "json"
    assert "pickle" not in json.dumps(fields).lower()
    payload = json.loads(fields["payload_json"])
    assert payload["playbook_name"] == "linux-baseline.yml"
    assert payload["extra_vars"] == {"region": "ap-east"}
    assert payload["runas_account_id"] == 1
    assert "password" not in payload
    assert "secret_id" not in payload
    assert "credential" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_job_center_adhoc_and_scheduler_tick(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"])
    stream = RecordingRedisStream()
    install_queue(stream)
    await seed_assets(session_factory)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/job-center/jobs/",
            json={
                "name": "uptime",
                "kind": "adhoc",
                "adhoc_module": "command",
                "adhoc_args": "uptime",
                "target_asset_ids": [1],
                "interval_seconds": 3600,
            },
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        assert created.json()["next_run_at"] is not None
        tick = client.post("/api/v1/job-center/scheduler/tick")
        assert tick.status_code == 202, tick.text
        assert tick.json()["queued"] == 1
        assert tick.json()["items"][0]["job_type"] == "job.adhoc"
        second = client.post("/api/v1/job-center/scheduler/tick")
        assert second.status_code == 202
        assert second.json()["queued"] == 0

    async with session_factory() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    assert job.next_run_at is not None
    assert job.interval_seconds == 3600

    fields = stream.calls[0][1]
    payload = json.loads(fields["payload_json"])
    assert fields["job_type"] == "job.adhoc"
    assert payload["module"] == "command"
    assert payload["args"] == "uptime"
    assert payload["job_id"] == job_id


@pytest.mark.asyncio
async def test_job_center_rejects_cross_tenant_targets_and_runas(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])
    install_queue(RecordingRedisStream())
    await seed_assets(session_factory)

    with TestClient(app) as client:
        playbook_id = _create_playbook(client)
        cross_asset = client.post(
            "/api/v1/job-center/jobs/",
            json={
                "name": "跨租户资产",
                "kind": "playbook",
                "playbook_id": playbook_id,
                "target_asset_ids": [2],
            },
        )
        assert cross_asset.status_code == 400
        assert cross_asset.json()["detail"] == "ASSET_NOT_FOUND"

        cross_runas = client.post(
            "/api/v1/job-center/jobs/",
            json={
                "name": "跨租户 runas",
                "kind": "playbook",
                "playbook_id": playbook_id,
                "target_asset_ids": [1],
                "runas_account_id": 2,
            },
        )
        assert cross_runas.status_code == 400
        assert cross_runas.json()["detail"] == "RUNAS_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_job_center_requires_automation_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["accounts:read"])
    with TestClient(app) as client:
        response = client.get("/api/v1/job-center/jobs/")
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_job_center_rejects_secret_vars_and_playbook_escape(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])
    install_queue(RecordingRedisStream())
    await seed_assets(session_factory)

    with TestClient(app) as client:
        secret_var = client.post(
            "/api/v1/job-center/variables/",
            json={"name": "bad", "extra_vars": {"password": "plain"}},
        )
        assert secret_var.status_code == 400
        assert secret_var.json()["detail"] == "AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"

        escape = client.post(
            "/api/v1/job-center/playbooks/",
            json={"name": "evil", "filename": "../outside.yml", "content": "---\n"},
        )
        assert escape.status_code == 400
        assert escape.json()["detail"] == "ANSIBLE_PLAYBOOK_NOT_ALLOWED"

        shell = client.post(
            "/api/v1/job-center/jobs/",
            json={
                "name": "shell",
                "kind": "adhoc",
                "adhoc_module": "shell",
                "adhoc_args": "uptime",
                "target_asset_ids": [1],
            },
        )
        assert shell.status_code == 400
        assert shell.json()["detail"] == "ADHOC_MODULE_NOT_ALLOWED"
