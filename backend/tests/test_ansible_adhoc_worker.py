"""#t77 job.adhoc worker：无凭据 inventory、租户隔离、JobExecution 回写。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun
from app.models.job_center import Job, JobExecution
from app.services.ansible_adhoc import (
    AnsibleAdhocRun,
    AnsibleAdhocWorkerHandler,
    LocalAnsibleAdhocRunner,
)
from app.services.ansible_playbook import AnsiblePlaybookTarget, AnsibleProcessLimits


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class RecordingAdhocRunner:
    def __init__(self) -> None:
        self.calls: list[AnsibleAdhocRun] = []

    async def run(self, adhoc: AnsibleAdhocRun) -> None:
        self.calls.append(adhoc)


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                tenant_id="tenant-a",
                name="prod-linux-1",
                address="203.0.113.10",
                platform_id=1,
                port=22,
                is_active=True,
            )
        )
        session.add(
            Account(
                id=9,
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_should_not_leak",
            )
        )
        session.add(
            Job(
                id=3,
                tenant_id="tenant-a",
                name="uptime",
                kind="adhoc",
                adhoc_module="command",
                adhoc_args="uptime",
                target_asset_ids=[1],
                extra_var_names=[],
            )
        )
        session.add(
            JobExecution(
                tenant_id="tenant-a",
                job_id=3,
                message_id="1700000000000-0",
                status="queued",
                requested_by="user-1",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_adhoc_handler_runs_without_credentials_and_syncs_execution(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(session_factory)
    runner = RecordingAdhocRunner()
    handler = AnsibleAdhocWorkerHandler(session_factory=session_factory, runner=runner)

    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={
            "module": "command",
            "args": "uptime",
            "target_asset_ids": [1],
            "check_mode": False,
            "runas_account_id": 9,
            "job_id": 3,
        },
        message_id="1700000000000-0",
    )

    assert len(runner.calls) == 1
    assert runner.calls[0].runas_username == "deploy"
    assert runner.calls[0].args == "uptime"
    for target in runner.calls[0].targets:
        assert not hasattr(target, "credential")
        assert not hasattr(target, "secret_id")

    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "1700000000000-0")
        execution = (
            await session.execute(
                select(JobExecution).where(JobExecution.message_id == "1700000000000-0")
            )
        ).scalar_one()
    assert run is not None
    assert run.job_type == "job.adhoc"
    assert run.status == "completed"
    assert execution is not None
    assert execution.status == "completed"


@pytest.mark.asyncio
async def test_local_adhoc_runner_renders_inventory_without_password(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    async def command_runner(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        process_limits: AnsibleProcessLimits,
    ) -> int:
        del cwd, process_limits
        inventory_index = args.index("-i") + 1
        inventory = json.loads(Path(args[inventory_index]).read_text(encoding="utf-8"))
        calls.append((args, inventory))
        assert "DATABASE_URL" not in env
        assert "password" not in json.dumps(inventory)
        return 0

    runner = LocalAnsibleAdhocRunner(runtime_root=runtime_root, command_runner=command_runner)
    await runner.run(
        AnsibleAdhocRun(
            tenant_id="tenant-a",
            requested_by="user-1",
            module="command",
            args="uptime",
            check_mode=False,
            runas_username="deploy",
            targets=[
                AnsiblePlaybookTarget(
                    id=1,
                    tenant_id="tenant-a",
                    name="prod-linux",
                    address="203.0.113.10",
                    port=22,
                    platform_id=1,
                )
            ],
        )
    )

    args, inventory = calls[0]
    assert args[:2] == ["ansible", "all"]
    assert "-m" in args and args[args.index("-m") + 1] == "command"
    assert "-a" in args and args[args.index("-a") + 1] == "uptime"
    assert inventory["all"]["hosts"]["asset_1"]["ansible_user"] == "deploy"
    assert "ansible_password" not in inventory["all"]["hosts"]["asset_1"]


@pytest.mark.asyncio
async def test_adhoc_handler_rejects_cross_tenant_asset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(session_factory)
    runner = RecordingAdhocRunner()
    handler = AnsibleAdhocWorkerHandler(session_factory=session_factory, runner=runner)
    with pytest.raises(ValueError, match="ASSET_NOT_FOUND"):
        await handler(
            tenant_id="tenant-b",
            requested_by="user-2",
            payload={"module": "command", "args": "uptime", "target_asset_ids": [1]},
            message_id="1700000000001-0",
        )
    assert runner.calls == []
