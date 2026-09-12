"""#t77 作业中心：CRUD、JSON-only 入队、runas、周期调度与无 pickle。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.job_center import OpsJob
from app.services.ansible_playbook import AnsiblePlaybookRun
from app.services.automation_worker import AutomationJobQueue, AutomationWorker
from app.services.job_center import next_run_at, sanitize_extra_vars
from app.services.job_center_scheduler import JobCenterScheduler
from app.services.job_center_worker import JobCenterWorkerHandler


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
        self._counter += 1
        self.calls.append((name, fields, maxlen))
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


def install_redis(stream: RecordingRedisStream) -> None:
    async def override_redis() -> AsyncGenerator[RecordingRedisStream, None]:
        yield stream

    app.dependency_overrides[get_redis] = override_redis


async def seed_targets(session_factory: async_sessionmaker[AsyncSession], *, tenant_id: str) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                name="prod",
                address="203.0.113.10",
                platform_id=1,
                tenant_id=tenant_id,
                is_active=True,
            )
        )
        session.add(
            Account(
                tenant_id=tenant_id,
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_1",
                status="active",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_job_center_playbook_run_enqueues_json_only_job_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:read", "automation:write"])
    stream = RecordingRedisStream()
    install_redis(stream)
    await seed_targets(session_factory, tenant_id="tenant-a")

    with TestClient(app) as client:
        playbook = client.post(
            "/api/v1/job-center/playbooks",
            json={"name": "基线", "filename": "linux-baseline.yml", "description": "patch"},
        )
        assert playbook.status_code == 201
        job = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "夜间基线",
                "job_kind": "playbook",
                "playbook_id": playbook.json()["id"],
                "extra_vars": {"cluster": "prod"},
                "target_asset_ids": [1],
                "runas_account_id": 1,
            },
        )
        assert job.status_code == 201, job.text
        variable = client.post(
            f"/api/v1/job-center/jobs/{job.json()['id']}/variables",
            json={"name": "release", "value": "2026.09"},
        )
        assert variable.status_code == 201
        run = client.post(f"/api/v1/job-center/jobs/{job.json()['id']}/run")
        assert run.status_code == 202
        assert run.json()["job_type"] == "job.playbook"

    assert len(stream.calls) == 1
    _name, fields, _maxlen = stream.calls[0]
    assert fields["payload_format"] == "json"
    assert "pickle" not in json.dumps(fields).lower()
    payload = json.loads(fields["payload_json"])
    assert payload == {"job_id": job.json()["id"]}
    assert "password" not in json.dumps(payload)
    assert "cluster" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_job_center_rejects_secret_extra_vars_and_pickle_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])
    install_redis(RecordingRedisStream())
    await seed_targets(session_factory, tenant_id="tenant-a")

    with TestClient(app) as client:
        playbook = client.post(
            "/api/v1/job-center/playbooks",
            json={"name": "基线", "filename": "linux-baseline.yml"},
        )
        denied = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "坏作业",
                "job_kind": "playbook",
                "playbook_id": playbook.json()["id"],
                "extra_vars": {"password": "s3cret"},
                "target_asset_ids": [1],
                "runas_account_id": 1,
            },
        )
        assert denied.status_code == 400
        assert denied.json()["detail"] == "AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"

        jinja = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "注入",
                "job_kind": "adhoc",
                "adhoc_module": "shell",
                "adhoc_command": "echo {{ lookup('env','SECRET') }}",
                "target_asset_ids": [1],
                "runas_account_id": 1,
            },
        )
        assert jinja.status_code == 400
        assert jinja.json()["detail"] == "JINJA_TEMPLATE_FORBIDDEN"


@pytest.mark.asyncio
async def test_job_center_scheduler_tick_enqueues_due_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:read", "automation:write"])
    stream = RecordingRedisStream()
    install_redis(stream)
    await seed_targets(session_factory, tenant_id="tenant-a")

    with TestClient(app) as client:
        playbook = client.post(
            "/api/v1/job-center/playbooks",
            json={"name": "基线", "filename": "linux-baseline.yml"},
        )
        created = client.post(
            "/api/v1/job-center/jobs",
            json={
                "name": "每分钟",
                "job_kind": "playbook",
                "playbook_id": playbook.json()["id"],
                "target_asset_ids": [1],
                "runas_account_id": 1,
                "cron_expr": "* * * * *",
                "timezone": "UTC",
            },
        )
        assert created.status_code == 201
        job_id = created.json()["id"]

    async with session_factory() as session:
        job = await session.get(OpsJob, job_id)
        assert job is not None
        job.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    with TestClient(app) as client:
        tick = client.post("/api/v1/job-center/scheduler/tick")
        assert tick.status_code == 200, tick.text
        assert tick.json()["enqueued"] == 1

    assert stream.calls
    payload = json.loads(stream.calls[0][1]["payload_json"])
    assert payload == {"job_id": job_id}


@pytest.mark.asyncio
async def test_job_center_worker_runs_playbook_without_secrets_in_argv(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory, tenant_id="tenant-a")
    async with session_factory() as session:
        job = OpsJob(
            tenant_id="tenant-a",
            name="手工",
            job_kind="playbook",
            playbook_id=None,
            extra_vars_json='{"cluster":"prod"}',
            target_asset_ids_json="[1]",
            runas_account_id=1,
            timezone="UTC",
            enabled=True,
            created_by="user-1",
        )
        from app.models.job_center import OpsPlaybook

        playbook = OpsPlaybook(
            tenant_id="tenant-a",
            name="基线",
            filename="linux-baseline.yml",
            description="",
            is_active=True,
        )
        session.add(playbook)
        await session.flush()
        job.playbook_id = playbook.id
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    captured: list[AnsiblePlaybookRun] = []

    class Runner:
        async def run(self, playbook: AnsiblePlaybookRun) -> None:
            captured.append(playbook)

    handler = JobCenterWorkerHandler(session_factory=session_factory, runner=Runner())
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"job_id": job_id},
        message_id="1700000000099-0",
    )
    assert captured[0].ansible_user == "deploy"
    assert captured[0].extra_vars == {"cluster": "prod"}
    assert captured[0].playbook_name == "linux-baseline.yml"
    assert captured[0].inline_playbook is None


@pytest.mark.asyncio
async def test_job_center_worker_adhoc_uses_inline_playbook(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory, tenant_id="tenant-a")
    async with session_factory() as session:
        job = OpsJob(
            tenant_id="tenant-a",
            name="批量uptime",
            job_kind="adhoc",
            adhoc_module="command",
            adhoc_command="uptime",
            extra_vars_json="{}",
            target_asset_ids_json="[1]",
            runas_account_id=1,
            timezone="UTC",
            enabled=True,
            created_by="user-1",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    captured: list[AnsiblePlaybookRun] = []

    class Runner:
        async def run(self, playbook: AnsiblePlaybookRun) -> None:
            captured.append(playbook)

    handler = JobCenterWorkerHandler(session_factory=session_factory, runner=Runner())
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"job_id": job_id},
        message_id="1700000000100-0",
    )
    assert captured[0].inline_playbook is not None
    assert "ansible.builtin.command" in captured[0].inline_playbook
    assert captured[0].extra_vars is not None
    assert captured[0].extra_vars["janusgate_adhoc_command"] == "uptime"


@pytest.mark.asyncio
async def test_job_center_queue_rejects_pickle_payload_format() -> None:
    stream = RecordingRedisStream()
    queue = AutomationJobQueue(redis=stream)
    await queue.enqueue(
        tenant_id="tenant-a",
        job_type="job.adhoc",
        requested_by="user-1",
        payload={"job_id": 1},
    )

    class Consumer(RecordingRedisStream):
        async def xreadgroup(self, *args: object, **kwargs: object) -> list[object]:
            del args, kwargs
            return [
                (
                    "janusgate:automation:jobs",
                    [
                        (
                            "1-0",
                            {
                                "tenant_id": "tenant-a",
                                "job_type": "job.adhoc",
                                "requested_by": "user-1",
                                "payload_json": "cos\nsystem\n(S'id'\ntR.",
                                "payload_format": "pickle",
                            },
                        )
                    ],
                )
            ]

        async def xack(self, name: str, groupname: str, message_id: str) -> int:
            del name, groupname, message_id
            raise AssertionError("pickle payload must not be acked")

    worker = AutomationWorker(
        redis=Consumer(),  # type: ignore[arg-type]
        handlers={"job.adhoc": _never_handler},
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_AUTOMATION_JOB_PAYLOAD_FORMAT"):
        await worker.run_once()


async def _never_handler(**kwargs: object) -> None:
    del kwargs
    raise AssertionError("handler must not run")


def test_sanitize_extra_vars_and_cron_next_run() -> None:
    assert sanitize_extra_vars({"region": "ap-east"}) == {"region": "ap-east"}
    with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
        sanitize_extra_vars({"token": "abc"})
    nxt = next_run_at(
        cron_expr="0 1 * * *",
        timezone="UTC",
        after=datetime(2026, 9, 12, 0, 30, tzinfo=UTC),
    )
    assert nxt == datetime(2026, 9, 12, 1, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_job_center_scheduler_service_enqueues_json_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory, tenant_id="tenant-a")
    async with session_factory() as session:
        job = OpsJob(
            tenant_id="tenant-a",
            name="周期",
            job_kind="adhoc",
            adhoc_module="shell",
            adhoc_command="true",
            extra_vars_json="{}",
            target_asset_ids_json="[1]",
            runas_account_id=1,
            cron_expr="* * * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=datetime.now(UTC) - timedelta(minutes=2),
            created_by="user-1",
        )
        session.add(job)
        await session.commit()

    stream = RecordingRedisStream()
    scheduler = JobCenterScheduler(
        session_factory=session_factory,
        queue=AutomationJobQueue(redis=stream),
    )
    result = await scheduler.tick()
    assert result.enqueued == 1
    fields = stream.calls[0][1]
    assert fields["payload_format"] == "json"
    assert json.loads(fields["payload_json"]) == {"job_id": 1}
    assert "pickle" not in json.dumps(fields).lower()
