"""#t73 account.push worker：成功/失败、AccountRisk、公钥解析、无凭据载荷。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import Account, AccountRisk
from app.models.asset import Asset, Platform
from app.models.automation import AutomationJobRun
from app.services.account_push import (
    PUSH_FAILED_COPY,
    AccountPushTarget,
    AccountPushWorkerHandler,
    PrivilegedSshCredential,
    extract_openssh_public_key,
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
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def unwrap(self, secret_id: str) -> str:
        self.calls.append(secret_id)
        return self.mapping[secret_id]


class RecordingPusher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[AccountPushTarget, PrivilegedSshCredential]] = []

    async def push(
        self,
        *,
        target: AccountPushTarget,
        privileged: PrivilegedSshCredential,
    ) -> None:
        self.calls.append((target, privileged))
        if self.fail:
            raise ValueError("SSH_PUSH_PROBE_FAILED")


async def seed_ssh_accounts(session_factory: async_sessionmaker[AsyncSession]) -> None:
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
                secret_id="sec_deploy_key",
                push_status="pushing",
            )
        )
        session.add(
            Account(
                id=2,
                tenant_id="tenant-a",
                asset_id=1,
                username="root",
                protocol="ssh",
                secret_id="sec_root",
                status="active",
            )
        )
        await session.commit()


def _ed25519_keypair() -> tuple[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode("utf-8")
    )
    return private_pem, public


@pytest.mark.asyncio
async def test_account_push_success(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await seed_ssh_accounts(session_factory)
    private_pem, public = _ed25519_keypair()
    secrets = FakeSecrets({"sec_deploy_key": private_pem, "sec_root": "root-pass"})
    pusher = RecordingPusher(fail=False)
    handler = AccountPushWorkerHandler(
        session_factory=session_factory, secrets=secrets, pusher=pusher
    )
    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"account_id": 1, "privileged_account_id": 2},
        message_id="msg-ok",
    )
    async with session_factory() as session:
        account = await session.get(Account, 1)
        run = await session.get(AutomationJobRun, "msg-ok")
        risks = list((await session.execute(select(AccountRisk))).scalars().all())
    assert account is not None and account.push_status == "success"
    assert account.last_push_message_id == "msg-ok"
    assert run is not None
    assert run.job_type == "account.push"
    assert run.status == "completed"
    assert run.reason is None
    assert risks == []
    assert secrets.calls == ["sec_deploy_key", "sec_root"]
    assert len(pusher.calls) == 1
    target, privileged = pusher.calls[0]
    assert target.username == "deploy"
    assert target.public_key.startswith("ssh-ed25519 ")
    assert target.public_key == public
    assert privileged.username == "root"
    assert privileged.password == "root-pass"
    assert privileged.private_key is None


@pytest.mark.asyncio
async def test_account_push_failure_creates_risk(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ssh_accounts(session_factory)
    _, public = _ed25519_keypair()
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets({"sec_deploy_key": public, "sec_root": "root-pass"}),
        pusher=RecordingPusher(fail=True),
    )
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_FAILED"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1, "privileged_account_id": 2},
            message_id="msg-fail",
        )
    async with session_factory() as session:
        account = await session.get(Account, 1)
        run = await session.get(AutomationJobRun, "msg-fail")
        risk = (await session.execute(select(AccountRisk))).scalar_one()
    assert account is not None and account.push_status == "failed"
    assert run is not None
    assert run.job_type == "account.push"
    assert run.status == "failed"
    assert run.reason == PUSH_FAILED_COPY
    assert "Traceback" not in (run.reason or "")
    assert risk.risk_type == "push_failed"
    assert risk.reason == PUSH_FAILED_COPY


@pytest.mark.asyncio
async def test_account_push_rejects_same_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_ssh_accounts(session_factory)
    _, public = _ed25519_keypair()
    handler = AccountPushWorkerHandler(
        session_factory=session_factory,
        secrets=FakeSecrets({"sec_deploy_key": public, "sec_root": "x"}),
        pusher=RecordingPusher(),
    )
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_PRIVILEGED_SAME_ACCOUNT"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"account_id": 1, "privileged_account_id": 1},
            message_id="msg-same",
        )


def test_extract_openssh_public_key_from_line() -> None:
    line = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterial comment"
    assert extract_openssh_public_key(line) == line


def test_extract_openssh_public_key_from_private_pem() -> None:
    private_pem, public = _ed25519_keypair()
    assert extract_openssh_public_key(private_pem) == public


def test_extract_openssh_public_key_rejects_password() -> None:
    with pytest.raises(ValueError, match="ACCOUNT_PUSH_PUBLIC_KEY_INVALID"):
        extract_openssh_public_key("plain-password")


@pytest.mark.asyncio
async def test_account_push_enqueue_rejects_credential_payload() -> None:
    class RecordingRedis:
        async def xadd(
            self, name: str, fields: dict[str, str], *, maxlen=None, approximate=True
        ) -> str:
            return "1-0"

    queue = AutomationJobQueue(redis=RecordingRedis())
    for key in ("password", "private_key", "secret", "token"):
        with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
            await queue.enqueue(
                tenant_id="tenant-a",
                job_type="account.push",
                requested_by="user-1",
                payload={"account_id": 1, "privileged_account_id": 2, key: "nope"},
            )
    assert "password" in SENSITIVE_PAYLOAD_KEYS
