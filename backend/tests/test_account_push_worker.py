"""#t73 account.push worker：成功、口令/未批准主机密钥 fail-closed、push_failed、无凭据载荷。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import Account, AccountRisk
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun
from app.services.account_push import (
    PUSH_DENIED_COPY,
    PUSH_FAILED_COPY,
    AccountPushTarget,
    AccountPushWorkerHandler,
    upsert_authorized_keys_content,
)
from app.services.automation_worker import SENSITIVE_PAYLOAD_KEYS, AutomationJobQueue


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
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls: list[str] = []

    async def unwrap(self, secret_id: str) -> str:
        self.calls.append(secret_id)
        return self.value


class FakeHostKeys:
    def __init__(self, approved: str = "ssh-ed25519 AAAAhost") -> None:
        self.approved = approved
        self.calls: list[tuple[str, str | int]] = []

    async def approved_public_key(self, *, tenant_id: str, asset_id: str | int) -> str:
        self.calls.append((tenant_id, asset_id))
        return self.approved


class RecordingPusher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def push(
        self,
        *,
        target: AccountPushTarget,
        private_key_plaintext: str,
        public_key_line: str,
        trusted_host_key: str,
    ) -> None:
        self.calls.append((target.username, public_key_line, trusted_host_key))
        del private_key_plaintext
        if self.fail:
            raise ValueError("SSH_PUSH_FAILED")



async def seed_ssh_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    credential_type: str | None = "private_key",
) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                name="prod",
                address="203.0.113.10",
                platform_id=1,
                tenant_id="tenant-a",
                port=22,
            )
        )
        session.add(
            Account(
                id=1,
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_deploy",
                credential_type=credential_type,
            )
        )
        await session.commit()


def test_upsert_authorized_keys_preserves_unrelated() -> None:
    existing = "ssh-ed25519 AAAAkeep comment\nssh-rsa AAAAother user@host\n"
    updated = upsert_authorized_keys_content(existing, "ssh-ed25519 AAAAnew me")
    assert "ssh-ed25519 AAAAkeep comment" in updated
    assert "ssh-rsa AAAAother user@host" in updated
    assert "ssh-ed25519 AAAAnew me" in updated
    again = upsert_authorized_keys_content(updated, "ssh-ed25519 AAAAnew other-comment")
    assert again.count("ssh-ed25519 AAAAnew") == 1


@pytest.mark.asyncio
async def test_account_push_success(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await seed_ssh_account(session_factory)
    # Use a real generated key so public_key derivation works.
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519")
    pem = key.export_private_key().decode()
    pub = key.export_public_key().decode().strip()
    secrets = FakeSecrets(pem)
    pusher = RecordingPusher()
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=secrets,
        host_keys=FakeHostKeys(),
        pusher=pusher,
    )
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"account_id": 1},
        message_id="msg-ok",
    )
    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "msg-ok")
        risks = list((await session.execute(select(AccountRisk))).scalars().all())
    assert run is not None
    assert run.job_type == "account.push"
    assert run.status == "completed"
    assert run.reason is None
    assert run.playbook_name == "deploy · prod · 成功"
    assert risks == []
    assert secrets.calls == ["sec_deploy"]
    assert len(pusher.calls) == 1
    assert pusher.calls[0][0] == "deploy"
    assert pusher.calls[0][1].split()[0:2] == pub.split()[0:2]
    assert "password" not in str(pusher.calls)
    assert pem not in (run.playbook_name or "")


@pytest.mark.asyncio
async def test_account_push_password_fails_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ssh_account(session_factory, credential_type="password")
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets("plaintext-password"),
        host_keys=FakeHostKeys(),
        pusher=RecordingPusher(),
    )
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_KEY_ONLY|ACCOUNT_PUSH_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1},
            message_id="msg-pw",
        )
    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "msg-pw")
        risk = (await session.execute(select(AccountRisk))).scalar_one()
    assert run is not None
    assert run.status == "failed"
    assert run.reason == PUSH_DENIED_COPY
    assert "无法连接" not in (run.reason or "")
    assert risk.risk_type == "push_failed"
    assert risk.reason == PUSH_DENIED_COPY


@pytest.mark.asyncio
async def test_account_push_password_secret_content_fails_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """credential_type 未标 password 时，仍按 Vault 明文内容 fail-closed。"""
    await seed_ssh_account(session_factory, credential_type="private_key")
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets("just-a-password"),
        host_keys=FakeHostKeys(),
        pusher=RecordingPusher(),
    )
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1},
            message_id="msg-pw2",
        )
    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "msg-pw2")
        risk = (await session.execute(select(AccountRisk))).scalar_one()
    assert run.reason == PUSH_DENIED_COPY
    assert risk.risk_type == "push_failed"


@pytest.mark.asyncio
async def test_account_push_unapproved_hostkey_fails_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ssh_account(session_factory)
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519")
    pem = key.export_private_key().decode()
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets(pem),
        host_keys=FakeHostKeys(approved=""),
        pusher=RecordingPusher(),
    )
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1},
            message_id="msg-hk",
        )
    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "msg-hk")
        risk = (await session.execute(select(AccountRisk))).scalar_one()
    assert run.reason == PUSH_DENIED_COPY
    assert "无法连接" not in (run.reason or "")
    assert risk.risk_type == "push_failed"
    assert risk.reason == PUSH_DENIED_COPY


@pytest.mark.asyncio
async def test_account_push_failure_records_push_failed_risk(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ssh_account(session_factory)
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519")
    pem = key.export_private_key().decode()
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets(pem),
        host_keys=FakeHostKeys(),
        pusher=RecordingPusher(fail=True),
    )
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1},
            message_id="msg-fail",
        )
    async with session_factory() as session:
        run = await session.get(AutomationJobRun, "msg-fail")
        risk = (await session.execute(select(AccountRisk))).scalar_one()
    assert run.reason == PUSH_FAILED_COPY
    assert risk.risk_type == "push_failed"
    assert risk.reason == PUSH_FAILED_COPY


@pytest.mark.asyncio
async def test_account_push_enqueue_rejects_credential_payload() -> None:
    class RecordingRedis:
        async def xadd(self, name: str, fields: dict[str, str], *, maxlen=None, approximate=True) -> str:
            return "1-0"

    queue = AutomationJobQueue(redis=RecordingRedis())
    for key in ("password", "private_key", "secret", "token"):
        with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
            await queue.enqueue(
                tenant_id="tenant-a",
                job_type="account.push",
                requested_by="user-1",
                payload={"account_id": 1, key: "nope"},
            )
    assert "private_key" in SENSITIVE_PAYLOAD_KEYS


@pytest.mark.asyncio
async def test_account_push_enqueue_allows_account_id_only() -> None:
    class RecordingRedis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def xadd(self, name: str, fields: dict[str, str], *, maxlen=None, approximate=True) -> str:
            self.calls.append((name, fields))
            return "9-0"

    redis = RecordingRedis()
    queue = AutomationJobQueue(redis=redis)
    job_id = await queue.enqueue(
        tenant_id="tenant-a",
        job_type="account.push",
        requested_by="user-1",
        payload={"account_id": 1},
    )
    assert job_id == "9-0"
    assert redis.calls[0][1]["job_type"] == "account.push"
    assert "secret" not in redis.calls[0][1]["payload_json"]
    assert "password" not in redis.calls[0][1]["payload_json"]
