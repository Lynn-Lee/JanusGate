"""#t73 account.verify worker：成功/失败、AccountRisk、AutomationJobRun、无凭据载荷。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import Account, AccountRisk
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun
from app.services.account_verify import (
    VERIFY_FAILED_COPY,
    AccountVerifyTarget,
    AccountVerifyWorkerHandler,
)
from app.services.automation_worker import AutomationJobQueue, SENSITIVE_PAYLOAD_KEYS


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class FakeSecrets:
    def __init__(self, value: str = "secret-plain") -> None:
        self.value = value
        self.calls: list[str] = []

    async def unwrap(self, secret_id: str) -> str:
        self.calls.append(secret_id)
        return self.value


class RecordingProbe:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def probe(self, *, target: AccountVerifyTarget, secret_plaintext: str) -> None:
        self.calls.append((target.username, secret_plaintext))
        if self.fail:
            raise ValueError("SSH_VERIFY_PROBE_FAILED")


async def seed_ssh_account(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(Asset(id=1, name="prod", address="203.0.113.10", platform_id=1, tenant_id="tenant-a", port=22))
        session.add(
            Account(
                id=1,
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_deploy",
                verify_status="verifying",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_account_verify_success(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await seed_ssh_account(session_factory)
    secrets = FakeSecrets()
    probe = RecordingProbe(fail=False)
    handler = AccountVerifyWorkerHandler(
        session_factory=session_factory, secrets=secrets, probe=probe
    )
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"account_id": 1},
        message_id="msg-ok",
    )
    async with session_factory() as session:
        account = await session.get(Account, 1)
        run = await session.get(AutomationJobRun, "msg-ok")
        risks = list((await session.execute(select(AccountRisk))).scalars().all())
    assert account is not None and account.verify_status == "success"
    assert account.last_verify_message_id == "msg-ok"
    assert run is not None
    assert run.job_type == "account.verify"
    assert run.status == "completed"
    assert run.reason is None
    assert risks == []
    assert secrets.calls == ["sec_deploy"]
    assert probe.calls == [("deploy", "secret-plain")]


@pytest.mark.asyncio
async def test_account_verify_failure_creates_risk_and_user_copy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ssh_account(session_factory)
    handler = AccountVerifyWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets(),
        probe=RecordingProbe(fail=True),
    )
    with pytest.raises(ValueError, match="ACCOUNT_VERIFY_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1},
            message_id="msg-fail",
        )
    async with session_factory() as session:
        account = await session.get(Account, 1)
        run = await session.get(AutomationJobRun, "msg-fail")
        risk = (await session.execute(select(AccountRisk))).scalar_one()
    assert account is not None and account.verify_status == "failed"
    assert run is not None
    assert run.job_type == "account.verify"
    assert run.status == "failed"
    assert run.reason == VERIFY_FAILED_COPY
    assert "Traceback" not in (run.reason or "")
    assert "无法连接" not in (run.reason or "")
    assert risk.risk_type == "verify_failed"
    assert risk.reason == VERIFY_FAILED_COPY
    assert risk.job_message_id == "msg-fail"


@pytest.mark.asyncio
async def test_account_verify_enqueue_rejects_credential_payload() -> None:
    class RecordingRedis:
        async def xadd(self, name: str, fields: dict[str, str], *, maxlen=None, approximate=True) -> str:
            return "1-0"

    queue = AutomationJobQueue(redis=RecordingRedis())
    for key in ("password", "private_key", "secret", "token"):
        with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
            await queue.enqueue(
                tenant_id="tenant-a",
                job_type="account.verify",
                requested_by="user-1",
                payload={"account_id": 1, key: "nope"},
            )
    assert "password" in SENSITIVE_PAYLOAD_KEYS
