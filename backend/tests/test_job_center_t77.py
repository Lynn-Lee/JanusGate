"""Phase 6 #t77 作业中心：作业定义、临时命令、周期任务、runas、JSON-only 队列。"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.automation import JobDefinition
from app.services.job_center import next_cron_run, parse_cron_expression, resolve_run_as_user_id


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
        return f"170000000000{len(self.calls)}-0"


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


def test_cron_every_five_minutes_advances() -> None:
    parse_cron_expression("*/5 * * * *")
    after = datetime(2026, 9, 10, 8, 1, tzinfo=UTC)
    assert next_cron_run("*/5 * * * *", after=after) == datetime(2026, 9, 10, 8, 5, tzinfo=UTC)


def test_runas_non_admin_cannot_impersonate() -> None:
    with pytest.raises(ValueError, match="JOB_RUNAS_FORBIDDEN"):
        resolve_run_as_user_id(
            actor={"id": "user-1", "permissions": ["automation:write"]},
            requested_run_as="other-user",
        )


def test_create_playbook_job_and_run_enqueues_json_only(session_factory: async_sessionmaker[AsyncSession]) -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "linux-baseline",
                "job_type": "ansible.playbook",
                "payload": {
                    "playbook_name": "linux-baseline.yml",
                    "target_asset_ids": [1, 2],
                    "check_mode": True,
                },
                "extra_variables": {"env": "prod"},
            },
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]

        secret = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "bad",
                "job_type": "ansible.playbook",
                "payload": {
                    "playbook_name": "linux-baseline.yml",
                    "target_asset_ids": [1],
                    "check_mode": False,
                    "password": "nope",
                },
            },
        )
        assert secret.status_code == 400

        run = client.post(f"/api/v1/job-center/jobs/{job_id}/run", json={"extra_variables": {"region": "sg"}})
        assert run.status_code == 202, run.text
        body = run.json()
        assert body["job_type"] == "ansible.playbook"
        assert body["status"] == "queued"
        assert body["extra_variables"]["env"] == "prod"
        assert body["extra_variables"]["region"] == "sg"
        assert body["extra_variables"].get("password") is None

        listed = client.get("/api/v1/job-center/jobs")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        runs = client.get(f"/api/v1/job-center/jobs/{job_id}/runs")
        assert runs.status_code == 200
        assert len(runs.json()) == 1

    fields = stream.calls[0][1]
    assert fields["payload_format"] == "json"
    assert fields["job_type"] == "ansible.playbook"
    assert "password" not in fields["payload_json"]
    app.dependency_overrides.clear()


def test_adhoc_and_batch_commands_map_to_playbook_queue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"])

    with TestClient(app) as client:
        adhoc = client.post(
            "/api/v1/job-center/adhoc",
            json={
                "name": "uptime",
                "command": "uptime",
                "target_asset_ids": [1],
                "save": True,
            },
        )
        assert adhoc.status_code == 202, adhoc.text
        assert adhoc.json()["job_type"] == "adhoc.command"

        batch = client.post(
            "/api/v1/job-center/adhoc",
            json={
                "name": "batch-uptime",
                "command": "uptime",
                "target_asset_ids": [1, 2],
            },
        )
        assert batch.status_code == 202
        assert batch.json()["job_type"] == "batch.command"

        forbidden = client.post(
            "/api/v1/job-center/adhoc",
            json={
                "name": "secret-cmd",
                "command": "uptime",
                "target_asset_ids": [1],
                "extra_variables": {"token": "abc"},
            },
        )
        assert forbidden.status_code == 400

    assert stream.calls[0][1]["job_type"] == "ansible.playbook"
    assert "adhoc-command.yml" in stream.calls[0][1]["payload_json"]
    assert "janusgate_adhoc_command" in stream.calls[0][1]["payload_json"]
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_runas_and_cron_dispatch(session_factory: async_sessionmaker[AsyncSession]) -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["admin"], user_id="admin-1")

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "nightly",
                "job_type": "ansible.playbook",
                "payload": {
                    "playbook_name": "linux-baseline.yml",
                    "target_asset_ids": [1],
                    "check_mode": False,
                },
                "cron_expression": "* * * * *",
                "run_as_user_id": "operator-9",
            },
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        assert created.json()["run_as_user_id"] == "operator-9"
        assert created.json()["next_run_at"]

    async with session_factory() as session:
        job = await session.get(JobDefinition, job_id)
        assert job is not None
        job.next_run_at = datetime.now(UTC) - timedelta(minutes=2)
        await session.commit()

    with TestClient(app) as client:
        dispatched = client.post("/api/v1/job-center/cron/dispatch")
        assert dispatched.status_code == 200
        assert job_id in dispatched.json()["dispatched_job_ids"]
        runs = client.get(f"/api/v1/job-center/jobs/{job_id}/runs")
        assert runs.status_code == 200
        assert runs.json()[0]["run_as_user_id"] == "operator-9"
        assert runs.json()[0]["requested_by"] == "operator-9"

    app.dependency_overrides.clear()


def test_job_center_requires_permission(session_factory: async_sessionmaker[AsyncSession]) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["assets:read"])
    with TestClient(app) as client:
        denied = client.get("/api/v1/job-center/jobs")
        assert denied.status_code == 403
    app.dependency_overrides.clear()
