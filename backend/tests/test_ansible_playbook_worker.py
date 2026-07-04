"""Phase 4 Ansible playbook worker handler tests."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun
from app.services.ansible_playbook import (
    AnsiblePlaybookRun,
    AnsiblePlaybookTarget,
    AnsiblePlaybookWorkerHandler,
    LocalAnsiblePlaybookRunner,
)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class RecordingPlaybookRunner:
    def __init__(self) -> None:
        self.calls: list[AnsiblePlaybookRun] = []

    async def run(self, playbook: AnsiblePlaybookRun) -> None:
        self.calls.append(playbook)


class FailingPlaybookRunner:
    async def run(self, playbook: AnsiblePlaybookRun) -> None:
        del playbook
        raise ValueError("ANSIBLE_PLAYBOOK_FAILED")


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
                port=22,
                username="deploy",
                credential="legacy-password",
                is_active=True,
            )
        )
        session.add(
            Asset(
                id=2,
                tenant_id="tenant-a",
                name="prod-linux-2",
                address="203.0.113.11",
                platform_id=1,
                port=22,
                username="deploy",
                credential="legacy-password",
                is_active=True,
            )
        )
        session.add(
            Asset(
                id=3,
                tenant_id="tenant-b",
                name="other-tenant",
                address="203.0.113.12",
                platform_id=1,
                port=22,
                is_active=True,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_playbook_handler_runs_current_tenant_assets_without_credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_assets(session_factory)
    runner = RecordingPlaybookRunner()
    handler = AnsiblePlaybookWorkerHandler(session_factory=session_factory, runner=runner)

    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={
            "playbook_name": "linux-baseline.yml",
            "target_asset_ids": [1, 2],
            "check_mode": True,
        },
        message_id="1700000000000-0",
    )

    assert runner.calls == [
        AnsiblePlaybookRun(
            tenant_id="tenant-a",
            requested_by="user-1",
            playbook_name="linux-baseline.yml",
            check_mode=True,
            targets=[
                AnsiblePlaybookTarget(
                    id=1,
                    tenant_id="tenant-a",
                    name="prod-linux-1",
                    address="203.0.113.10",
                    port=22,
                    platform_id=1,
                ),
                AnsiblePlaybookTarget(
                    id=2,
                    tenant_id="tenant-a",
                    name="prod-linux-2",
                    address="203.0.113.11",
                    port=22,
                    platform_id=1,
                ),
            ],
        )
    ]
    for target in runner.calls[0].targets:
        assert not hasattr(target, "credential")


@pytest.mark.asyncio
async def test_playbook_handler_persists_completion_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_assets(session_factory)
    handler = AnsiblePlaybookWorkerHandler(
        session_factory=session_factory,
        runner=RecordingPlaybookRunner(),
    )

    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={
            "playbook_name": "linux-baseline.yml",
            "target_asset_ids": [1, 2],
            "check_mode": True,
        },
        message_id="1700000000000-0",
    )

    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "1700000000000-0")

    assert run is not None
    assert run.tenant_id == "tenant-a"
    assert run.job_type == "ansible.playbook"
    assert run.status == "completed"
    assert run.requested_by == "user-1"
    assert run.playbook_name == "linux-baseline.yml"
    assert run.target_count == 2
    assert run.error_code is None


@pytest.mark.asyncio
async def test_playbook_handler_persists_failure_status_without_secret_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_assets(session_factory)
    handler = AnsiblePlaybookWorkerHandler(
        session_factory=session_factory,
        runner=FailingPlaybookRunner(),
    )

    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={
                "playbook_name": "linux-baseline.yml",
                "target_asset_ids": [1],
                "check_mode": False,
            },
            message_id="1700000000000-0",
        )

    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "1700000000000-0")

    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "ANSIBLE_PLAYBOOK_FAILED"
    assert run.playbook_name == "linux-baseline.yml"
    assert run.target_count == 1
    assert not hasattr(run, "payload")
    assert not hasattr(run, "stdout")
    assert not hasattr(run, "stderr")


@pytest.mark.asyncio
async def test_playbook_handler_rejects_cross_tenant_target(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_assets(session_factory)
    runner = RecordingPlaybookRunner()
    handler = AnsiblePlaybookWorkerHandler(session_factory=session_factory, runner=runner)

    with pytest.raises(ValueError, match="ASSET_NOT_FOUND"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={
                "playbook_name": "linux-baseline.yml",
                "target_asset_ids": [1, 3],
                "check_mode": False,
            },
            message_id="1700000000000-0",
        )

    assert runner.calls == []


@pytest.mark.asyncio
async def test_local_ansible_runner_renders_inventory_and_runs_check_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playbook_root = tmp_path / "playbooks"
    runtime_root = tmp_path / "runtime"
    playbook_root.mkdir()
    runtime_root.mkdir()
    (playbook_root / "linux-baseline.yml").write_text("---\n- hosts: all\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db/janusgate")
    calls: list[tuple[list[str], Path, dict[str, str], dict[str, object]]] = []

    async def command_runner(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> int:
        inventory_index = args.index("-i") + 1
        inventory = json.loads(Path(args[inventory_index]).read_text(encoding="utf-8"))
        calls.append((args, cwd, env, inventory))
        return 0

    runner = LocalAnsiblePlaybookRunner(
        playbook_root=playbook_root,
        runtime_root=runtime_root,
        command_runner=command_runner,
    )

    await runner.run(
        AnsiblePlaybookRun(
            tenant_id="tenant-a",
            requested_by="user-1",
            playbook_name="linux-baseline.yml",
            check_mode=True,
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

    assert len(calls) == 1
    args, cwd, env, inventory = calls[0]
    assert args[:2] == ["ansible-playbook", str(playbook_root / "linux-baseline.yml")]
    assert "--check" in args
    assert cwd.is_relative_to(runtime_root)
    assert "DATABASE_URL" not in env
    assert inventory == {
        "all": {
            "hosts": {
                "asset_1": {
                    "ansible_host": "203.0.113.10",
                    "ansible_port": 22,
                    "janusgate_asset_id": 1,
                    "janusgate_asset_name": "prod-linux",
                    "janusgate_platform_id": 1,
                    "janusgate_tenant_id": "tenant-a",
                }
            }
        }
    }


@pytest.mark.asyncio
async def test_local_ansible_runner_rejects_playbook_path_traversal(tmp_path: Path) -> None:
    playbook_root = tmp_path / "playbooks"
    runtime_root = tmp_path / "runtime"
    playbook_root.mkdir()
    runtime_root.mkdir()
    runner = LocalAnsiblePlaybookRunner(playbook_root=playbook_root, runtime_root=runtime_root)

    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        await runner.run(
            AnsiblePlaybookRun(
                tenant_id="tenant-a",
                requested_by="user-1",
                playbook_name="../outside.yml",
                check_mode=False,
                targets=[],
            )
        )
