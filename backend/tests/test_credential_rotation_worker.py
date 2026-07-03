"""Phase 4 credential rotation worker tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import Account, CredentialRotation
from app.models.asset import Asset, Platform
from app.services.credential_rotation import (
    CredentialRotationResult,
    CredentialRotationWorker,
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


class RecordingRotator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def rotate(self, account: Account, rotation: CredentialRotation) -> CredentialRotationResult:
        self.calls.append(f"{account.username}:{rotation.id}")
        if self.fail:
            raise ValueError("CONNECTOR_ROTATION_FAILED")
        return CredentialRotationResult(secret_id=f"{account.secret_id}:v2")


async def seed_account_with_rotation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: str = "scheduled",
    scheduled_at: datetime | None = None,
) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(Asset(id=1, name="prod-linux", address="203.0.113.10", platform_id=1))
        session.add(
            Account(
                id=1,
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
                rotation_policy="manual",
            )
        )
        session.add(
            CredentialRotation(
                id=1,
                tenant_id="tenant-a",
                account_id=1,
                status=status,
                reason="quarterly",
                requested_by="user-1",
                scheduled_at=scheduled_at,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_rotation_worker_executes_due_scheduled_rotation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_account_with_rotation(
        session_factory,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    rotator = RecordingRotator()

    async with session_factory() as session:
        worker = CredentialRotationWorker(session=session, rotator=rotator)
        processed = await worker.run_due_rotations(now=datetime.now(UTC), limit=10)

        account = await session.get(Account, 1)
        rotation = await session.get(CredentialRotation, 1)

    assert processed == 1
    assert rotator.calls == ["deploy:1"]
    assert account is not None
    assert account.secret_id == "sec_tenant_a_deploy:v2"
    assert rotation is not None
    assert rotation.status == "completed"
    assert rotation.previous_secret_id == "sec_tenant_a_deploy"
    assert rotation.new_secret_id == "sec_tenant_a_deploy:v2"


@pytest.mark.asyncio
async def test_rotation_worker_marks_failed_without_changing_account_secret(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_account_with_rotation(
        session_factory,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    rotator = RecordingRotator(fail=True)

    async with session_factory() as session:
        worker = CredentialRotationWorker(session=session, rotator=rotator)
        processed = await worker.run_due_rotations(now=datetime.now(UTC), limit=10)

        account = await session.get(Account, 1)
        rotation = await session.get(CredentialRotation, 1)

    assert processed == 1
    assert account is not None
    assert account.secret_id == "sec_tenant_a_deploy"
    assert rotation is not None
    assert rotation.status == "failed"
    assert rotation.previous_secret_id == "sec_tenant_a_deploy"
    assert rotation.new_secret_id is None
    assert rotation.error_code == "CONNECTOR_ROTATION_FAILED"


@pytest.mark.asyncio
async def test_rotation_worker_rolls_back_completed_rotation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_account_with_rotation(
        session_factory,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    rotator = RecordingRotator()

    async with session_factory() as session:
        worker = CredentialRotationWorker(session=session, rotator=rotator)
        await worker.run_due_rotations(now=datetime.now(UTC), limit=10)
        rolled_back = await worker.rollback_completed_rotation(rotation_id=1)

        account = await session.get(Account, 1)
        rotation = await session.get(CredentialRotation, 1)

    assert rolled_back is True
    assert account is not None
    assert account.secret_id == "sec_tenant_a_deploy"
    assert rotation is not None
    assert rotation.status == "rolled_back"
    assert rotation.previous_secret_id == "sec_tenant_a_deploy"
    assert rotation.new_secret_id == "sec_tenant_a_deploy:v2"


@pytest.mark.asyncio
async def test_rotation_worker_ignores_future_or_non_scheduled_rotations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_account_with_rotation(
        session_factory,
        status="completed",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    rotator = RecordingRotator()

    async with session_factory() as session:
        worker = CredentialRotationWorker(session=session, rotator=rotator)
        processed = await worker.run_due_rotations(now=datetime.now(UTC), limit=10)

        rotation_ids = [row.id for row in (await session.execute(select(CredentialRotation))).scalars()]

    assert processed == 0
    assert rotator.calls == []
    assert rotation_ids == [1]
