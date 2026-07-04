"""Phase 4 Ansible playbook worker handler tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.asset import Asset, Platform
from app.services.ansible_playbook import (
    AnsiblePlaybookRun,
    AnsiblePlaybookTarget,
    AnsiblePlaybookWorkerHandler,
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
