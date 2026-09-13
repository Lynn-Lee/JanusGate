"""#t77 作业中心：消毒、队列 payload、租户隔离、cron tick 与 worker 无凭据。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.account import Account
from app.models.acl import CommandFilterAclModel, CommandFilterAction, CommandGroupModel
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun
from app.models.ops import OpsJob, OpsJobExecution
from app.services.ansible_playbook import AnsiblePlaybookRun, LocalAnsiblePlaybookRunner
from app.services.ops_sanitize import (
    ADHOC_PLAYBOOK,
    OpsConfigError,
    extra_vars,
    next_cron_run,
    playbook_relative_path,
)
from app.services.ops_worker import OpsJobWorkerHandler


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


class RecordingPlaybookRunner:
    def __init__(self) -> None:
        self.calls: list[AnsiblePlaybookRun] = []

    async def run(self, playbook: AnsiblePlaybookRun) -> None:
        self.calls.append(playbook)


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
    app.dependency_overrides[get_read_db] = override_db


async def seed_targets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = "tenant-a",
) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                tenant_id=tenant_id,
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                port=22,
                is_active=True,
            )
        )
        session.add(
            Account(
                id=1,
                tenant_id=tenant_id,
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
                status="active",
            )
        )
        await session.commit()


async def seed_command_acl(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    action: CommandFilterAction,
    pattern: str,
    acl_id: str = "acl-ops",
    group_id: str = "grp-ops",
) -> None:
    async with session_factory() as session:
        session.add(
            CommandGroupModel(
                id=group_id,
                tenant_id="tenant-a",
                name=group_id,
                match_type="command",
                patterns_json=json.dumps([pattern]),
                is_active=True,
            )
        )
        session.add(
            CommandFilterAclModel(
                id=acl_id,
                tenant_id="tenant-a",
                name=acl_id,
                priority=10,
                action=action,
                reviewer_subject_ids_json=json.dumps(["reviewer-1"]),
                subject_ids_json=json.dumps(["*"]),
                asset_ids_json=json.dumps(["*"]),
                account_ids_json=json.dumps(["*"]),
                command_group_ids_json=json.dumps([group_id]),
                is_active=True,
            )
        )
        await session.commit()


def test_playbook_relative_path_rejects_escape() -> None:
    with pytest.raises(OpsConfigError, match="OPS_PLAYBOOK_PATH_INVALID"):
        playbook_relative_path("../secret.yml")
    with pytest.raises(OpsConfigError, match="OPS_PLAYBOOK_PATH_INVALID"):
        playbook_relative_path("/etc/passwd.yml")
    assert playbook_relative_path("janusgate-adhoc.yml") == "janusgate-adhoc.yml"


def test_extra_vars_rejects_secrets_and_jinja() -> None:
    with pytest.raises(OpsConfigError, match="OPS_EXTRA_VARS_SECRET"):
        extra_vars({"password": "nope"})
    with pytest.raises(OpsConfigError, match="OPS_EXTRA_VARS_SECRET"):
        extra_vars({"janusgate_token": "nope"})
    with pytest.raises(OpsConfigError, match="OPS_JINJA_NOT_ALLOWED"):
        extra_vars({"pkg": "{{ lookup('env','SECRET') }}"})
    assert extra_vars({"retries": 2, "check": True}) == {"retries": 2, "check": True}


def test_next_cron_run_advances_one_minute() -> None:
    after = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    assert next_cron_run("* * * * *", after=after) == datetime(2026, 9, 13, 10, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_ops_api_enqueues_execution_id_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory)
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/ops/playbooks",
            json={"name": "基线", "relative_path": "janusgate-adhoc.yml", "description": "内置"},
        )
        assert created.status_code == 201, created.text
        playbook_id = created.json()["id"]
        job = client.post(
            "/api/v1/ops/jobs",
            json={
                "name": "基线作业",
                "job_type": "playbook",
                "playbook_id": playbook_id,
                "runas_account_id": 1,
                "target_asset_ids": [1],
                "extra_vars": {"retries": 1},
                "variables": [{"name": "retries", "default_value": "1", "required": False}],
            },
        )
        assert job.status_code == 201, job.text
        run = client.post(f"/api/v1/ops/jobs/{job.json()['id']}/run", json={})
        assert run.status_code == 202, run.text
        listed = client.get("/api/v1/ops/jobs")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

    app.dependency_overrides.clear()
    assert len(stream.calls) == 1
    _, fields, _ = stream.calls[0]
    assert fields["job_type"] == "ops.job"
    payload = json.loads(fields["payload_json"])
    assert payload == {"check_mode": False, "execution_id": run.json()["id"]}
    assert "password" not in json.dumps(fields).lower()
    assert "sec_tenant_a_deploy" not in json.dumps(fields)
    assert "retries" not in fields["payload_json"]


@pytest.mark.asyncio
async def test_ops_api_rejects_secret_extra_vars_and_escaped_playbook(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory)
    app.dependency_overrides[get_redis] = lambda: RecordingRedisStream()
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write"])

    with TestClient(app) as client:
        escaped = client.post(
            "/api/v1/ops/playbooks",
            json={"name": "逃逸", "relative_path": "../secret.yml"},
        )
        assert escaped.status_code == 400
        assert escaped.json()["code"] == "OPS_PLAYBOOK_PATH_INVALID"
        created = client.post(
            "/api/v1/ops/playbooks",
            json={"name": "基线", "relative_path": "linux-baseline.yml"},
        )
        secret_job = client.post(
            "/api/v1/ops/jobs",
            json={
                "name": "危险作业",
                "job_type": "playbook",
                "playbook_id": created.json()["id"],
                "runas_account_id": 1,
                "target_asset_ids": [1],
                "extra_vars": {"password": "plain-secret"},
            },
        )
        assert secret_job.status_code == 400
        assert secret_job.json()["code"] == "OPS_EXTRA_VARS_SECRET"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_adhoc_filters_deny_and_review(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory)
    await seed_command_acl(session_factory, action=CommandFilterAction.REJECT, pattern="rm")
    app.dependency_overrides[get_redis] = lambda: RecordingRedisStream()
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write"])

    with TestClient(app) as client:
        denied = client.post(
            "/api/v1/ops/adhoc",
            json={
                "runas_account_id": 1,
                "target_asset_ids": [1],
                "adhoc_module": "command",
                "command": "rm -rf /tmp/x",
            },
        )
        assert denied.status_code == 400
        assert denied.json()["code"] == "OPS_COMMAND_DENIED"
        jinja = client.post(
            "/api/v1/ops/adhoc",
            json={
                "runas_account_id": 1,
                "target_asset_ids": [1],
                "command": "echo {{ ansible_password }}",
            },
        )
        assert jinja.status_code == 400
        assert jinja.json()["code"] == "OPS_JINJA_NOT_ALLOWED"

    app.dependency_overrides.clear()

    await seed_command_acl(
        session_factory,
        action=CommandFilterAction.REVIEW,
        pattern="systemctl",
        acl_id="acl-review",
        group_id="grp-review",
    )
    app.dependency_overrides[get_redis] = lambda: RecordingRedisStream()
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write"])
    with TestClient(app) as client:
        review = client.post(
            "/api/v1/ops/adhoc",
            json={
                "runas_account_id": 1,
                "target_asset_ids": [1],
                "command": "systemctl restart nginx",
            },
        )
        assert review.status_code == 400
        assert review.json()["code"] == "OPS_COMMAND_REVIEW_REQUIRED"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_tenant_isolation_and_scheduler_tick(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_targets(session_factory)
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream
    install_db(session_factory)
    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])

    with TestClient(app) as client:
        playbook = client.post(
            "/api/v1/ops/playbooks",
            json={"name": "基线", "relative_path": "linux-baseline.yml"},
        )
        job = client.post(
            "/api/v1/ops/jobs",
            json={
                "name": "周期作业",
                "job_type": "playbook",
                "playbook_id": playbook.json()["id"],
                "runas_account_id": 1,
                "target_asset_ids": [1],
                "cron_expr": "* * * * *",
            },
        )
        assert job.status_code == 201, job.text
        job_id = job.json()["id"]

    async with session_factory() as session:
        stored = await session.get(OpsJob, job_id)
        assert stored is not None
        stored.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    install_user(tenant_id="tenant-b", permissions=["automation:read"])
    with TestClient(app) as client:
        listed = client.get("/api/v1/ops/jobs")
        assert listed.status_code == 200
        assert listed.json() == {"items": [], "total": 0}

    install_user(tenant_id="tenant-a", permissions=["automation:write", "automation:read"])
    with TestClient(app) as client:
        ticked = client.post("/api/v1/ops/scheduler/tick")
        assert ticked.status_code == 200, ticked.text
        assert len(ticked.json()["queued_execution_ids"]) == 1
        executions = client.get("/api/v1/ops/executions")
        assert executions.json()["total"] == 1

    app.dependency_overrides.clear()
    assert stream.calls[-1][1]["job_type"] == "ops.job"


@pytest.mark.asyncio
async def test_ops_worker_loads_spec_without_credentials(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await seed_targets(session_factory)
    async with session_factory() as session:
        session.add(
            OpsJobExecution(
                tenant_id="tenant-a",
                job_type="adhoc",
                playbook_name=ADHOC_PLAYBOOK,
                command="uptime",
                adhoc_module="command",
                target_asset_ids_json="[1]",
                extra_vars_json="{}",
                runas_account_id=1,
                requested_by="user-1",
                check_mode=True,
                status="queued",
            )
        )
        await session.commit()

    runner = RecordingPlaybookRunner()
    handler = OpsJobWorkerHandler(session_factory=session_factory, runner=runner)
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"execution_id": 1, "check_mode": True},
        message_id="1700000000000-0",
    )

    assert len(runner.calls) == 1
    run = runner.calls[0]
    assert run.playbook_name == ADHOC_PLAYBOOK
    assert run.ansible_user == "deploy"
    assert run.extra_vars["adhoc_command"] == "uptime"
    assert "password" not in json.dumps(run.extra_vars)
    assert "sec_tenant_a_deploy" not in json.dumps(run.__dict__, default=str)
    async with session_factory() as session:
        execution = await session.get(OpsJobExecution, 1)
        job_run = await session.get(AutomationJobRun, "1700000000000-0")
    assert execution is not None and execution.status == "completed"
    assert job_run is not None
    assert job_run.job_type == "ops.job"
    assert job_run.status == "completed"

    playbook_root = tmp_path / "playbooks"
    runtime_root = tmp_path / "runtime"
    playbook_root.mkdir()
    runtime_root.mkdir()
    (playbook_root / ADHOC_PLAYBOOK).write_text("---\n- hosts: all\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    async def command_runner(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        process_limits: Any,
    ) -> int:
        del cwd, process_limits
        extra_index = args.index("--extra-vars") + 1
        extra_path = Path(str(args[extra_index]).removeprefix("@"))
        inventory_index = args.index("-i") + 1
        captured["args"] = args
        captured["env"] = env
        captured["extra"] = json.loads(extra_path.read_text(encoding="utf-8"))
        captured["inventory"] = json.loads(Path(args[inventory_index]).read_text(encoding="utf-8"))
        return 0

    local = LocalAnsiblePlaybookRunner(
        playbook_root=playbook_root,
        runtime_root=runtime_root,
        command_runner=command_runner,
    )
    await local.run(run)
    assert captured["extra"] == {"adhoc_command": "uptime", "adhoc_module": "command"}
    assert captured["inventory"]["all"]["hosts"]["asset_1"]["ansible_user"] == "deploy"
    assert "password" not in json.dumps(captured)
    assert "DATABASE_URL" not in captured["env"]
    assert any(item == "--check" for item in captured["args"])
    assert str(captured["args"][captured["args"].index("--extra-vars") + 1]).startswith("@")
