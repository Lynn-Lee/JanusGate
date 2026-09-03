"""#t73 账号自动化 worker 与治理逻辑测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.account import (
    Account,
    AccountAutomationRun,
    AccountBackup,
    AccountRisk,
    AccountTemplate,
    CredentialRotation,
)
from app.models.asset import Asset, Platform
from app.services.account_automation import (
    ACCOUNT_JOB_TYPES,
    AccountAutomationExecutor,
    AccountAutomationResult,
    AccountAutomationTarget,
    AccountAutomationWorkerHandler,
    AccountRiskDraft,
    BoundAccountJobHandler,
    DiscoveredAccount,
    LocalPolicyAccountExecutor,
    bind_account_job_handler,
)
from app.services.automation_worker import ALLOWED_JOB_TYPES, JsonValue


@dataclass
class RecordingExecutor:
    calls: list[tuple[str, str]] = field(default_factory=list)
    gather_usernames: tuple[str, ...] = ("deploy", "root")

    async def push_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult:
        self.calls.append(("push", target.username))
        del admin
        return AccountAutomationResult(
            summary=f"pushed user={target.username}",
            new_secret_plaintext="N3w!Password-ok12",
        )

    async def change_secret(
        self, target: AccountAutomationTarget, *, new_password: str
    ) -> AccountAutomationResult:
        assert new_password
        self.calls.append(("change_secret", target.username))
        return AccountAutomationResult(summary="secret_changed")

    async def verify_account(self, target: AccountAutomationTarget) -> AccountAutomationResult:
        self.calls.append(("verify", target.username))
        return AccountAutomationResult(summary="account_verified")

    async def remove_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult:
        self.calls.append(("remove", target.username))
        del admin
        return AccountAutomationResult(summary=f"removed user={target.username}")

    async def gather_accounts(
        self, target: AccountAutomationTarget
    ) -> tuple[AccountAutomationResult, list[DiscoveredAccount]]:
        self.calls.append(("gather", target.username))
        discovered = [
            DiscoveredAccount(username=name, uid=0 if name == "root" else 1000, home_dir=None, shell=None)
            for name in self.gather_usernames
        ]
        risks = tuple(
            AccountRiskDraft(
                username="root",
                risk_type="privileged",
                severity="high",
                detail="privileged account discovered on host",
                asset_id=target.asset_id,
            )
            for name in self.gather_usernames
            if name == "root"
        )
        return (
            AccountAutomationResult(summary=f"gathered accounts={len(discovered)}", risks=risks),
            discovered,
        )

    async def verify_gateway_account(
        self, target: AccountAutomationTarget
    ) -> AccountAutomationResult:
        self.calls.append(("verify_gateway", target.username))
        return AccountAutomationResult(summary="gateway_account_present")

    async def check_account(
        self, target: AccountAutomationTarget, *, plaintext: str | None
    ) -> AccountAutomationResult:
        self.calls.append(("check", target.username))
        del plaintext
        return AccountAutomationResult(summary="checked risks=0")

    async def backup_account(self, target: AccountAutomationTarget) -> AccountAutomationResult:
        self.calls.append(("backup", target.username))
        return AccountAutomationResult(summary="account_metadata_backed_up")


class MemorySecretStore:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self.secrets = dict(secrets or {})
        self.rotated: list[tuple[str, str]] = []

    async def unwrap(self, secret_id: str) -> str:
        if secret_id not in self.secrets:
            raise ValueError("SECRET_NOT_FOUND")
        return self.secrets[secret_id]

    async def rotate(self, secret_id: str, new_plaintext: str) -> object:
        self.secrets[secret_id] = new_plaintext
        self.rotated.append((secret_id, new_plaintext))
        return SimpleNamespace(secret_id=secret_id)

    async def create_secret(self, name: str, plaintext: str) -> object:
        secret_id = f"sec_{name.replace(':', '_')}"
        self.secrets[secret_id] = plaintext
        return SimpleNamespace(secret_id=secret_id)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_inventory(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                tenant_id="tenant-a",
                port=22,
            )
        )
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_deploy",
            )
        )
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="root",
                protocol="ssh",
                secret_id="sec_root",
            )
        )
        session.add(
            AccountTemplate(
                tenant_id="tenant-a",
                name="ops-user",
                username="ops",
                protocol="ssh",
                privileged=False,
                groups_json='["sudo"]',
            )
        )
        await session.commit()


def _handler(
    session_factory: async_sessionmaker[AsyncSession],
    executor: AccountAutomationExecutor,
    secrets: MemorySecretStore | None = None,
) -> AccountAutomationWorkerHandler:
    return AccountAutomationWorkerHandler(
        session_factory=session_factory,
        executor=executor,
        secrets_store=secrets,
    )


async def _run(
    handler: AccountAutomationWorkerHandler,
    *,
    job_type: str,
    payload: dict[str, JsonValue],
    message_id: str = "1-0",
) -> None:
    bound: BoundAccountJobHandler = bind_account_job_handler(handler, job_type)
    await bound(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload=payload,
        message_id=message_id,
    )


def test_allowed_job_types_include_account_automation() -> None:
    assert ACCOUNT_JOB_TYPES <= ALLOWED_JOB_TYPES
    assert {
        "account.push",
        "account.change_secret",
        "account.verify",
        "account.remove",
        "account.gather",
        "account.verify_gateway",
        "account.check",
        "account.backup",
    } == ACCOUNT_JOB_TYPES


@pytest.mark.asyncio
async def test_check_account_writes_privileged_and_weak_password_risks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    secrets = MemorySecretStore({"sec_root": "weak"})
    handler = _handler(session_factory, LocalPolicyAccountExecutor(), secrets)
    await _run(handler, job_type="account.check", payload={"account_id": 2}, message_id="check-1")

    async with session_factory() as session:
        risks = (await session.execute(select(AccountRisk).order_by(AccountRisk.risk_type))).scalars().all()
        types = {risk.risk_type for risk in risks}
        assert types == {"privileged", "weak_password"}
        assert all(risk.status == "open" for risk in risks)
        run = (await session.execute(select(AccountAutomationRun))).scalar_one()
        assert run.status == "completed"
        assert run.job_type == "account.check"
        assert "password" not in (run.result_summary or "").lower()


@pytest.mark.asyncio
async def test_backup_account_stores_metadata_without_plaintext(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    handler = _handler(session_factory, LocalPolicyAccountExecutor())
    await _run(handler, job_type="account.backup", payload={"account_id": 1}, message_id="backup-1")

    async with session_factory() as session:
        backup = (await session.execute(select(AccountBackup))).scalar_one()
        assert backup.username == "deploy"
        assert backup.secret_id_ref == "sec_deploy"
        assert "weak" not in backup.metadata_json
        assert "password" not in backup.metadata_json.lower()


@pytest.mark.asyncio
async def test_verify_gateway_account_accepts_managed_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    handler = _handler(session_factory, LocalPolicyAccountExecutor())
    await _run(
        handler,
        job_type="account.verify_gateway",
        payload={"account_id": 1},
        message_id="gw-1",
    )
    async with session_factory() as session:
        run = (await session.execute(select(AccountAutomationRun))).scalar_one()
        assert run.result_summary == "gateway_account_present"


@pytest.mark.asyncio
async def test_change_secret_rotates_vault_and_writes_rotation_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    secrets = MemorySecretStore({"sec_deploy": "OldPassw0rd!"})
    executor = RecordingExecutor()
    handler = _handler(session_factory, executor, secrets)
    await _run(
        handler,
        job_type="account.change_secret",
        payload={"account_id": 1, "reason": "quarterly"},
        message_id="chg-1",
    )
    assert executor.calls == [("change_secret", "deploy")]
    assert secrets.rotated and secrets.rotated[0][0] == "sec_deploy"
    assert secrets.rotated[0][1] != "OldPassw0rd!"
    async with session_factory() as session:
        rotation = (await session.execute(select(CredentialRotation))).scalar_one()
        assert rotation.status == "completed"
        assert rotation.reason == "quarterly"


@pytest.mark.asyncio
async def test_push_account_creates_custody_record_from_template(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    secrets = MemorySecretStore({"sec_root": "Adm1n!Pass-ok"})
    executor = RecordingExecutor()
    handler = _handler(session_factory, executor, secrets)
    await _run(
        handler,
        job_type="account.push",
        payload={"asset_id": 1, "template_id": 1},
        message_id="push-1",
    )
    assert executor.calls == [("push", "ops")]
    async with session_factory() as session:
        account = (
            await session.execute(select(Account).where(Account.username == "ops"))
        ).scalar_one()
        assert account.secret_id.startswith("sec_")
        assert account.secret_id in secrets.secrets


@pytest.mark.asyncio
async def test_remove_account_marks_custody_removed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    handler = _handler(session_factory, RecordingExecutor())
    await _run(handler, job_type="account.remove", payload={"account_id": 1}, message_id="rm-1")
    async with session_factory() as session:
        account = await session.get(Account, 1)
        assert account is not None
        assert account.status == "removed"


@pytest.mark.asyncio
async def test_gather_accounts_records_privileged_and_zombie_risks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    executor = RecordingExecutor(gather_usernames=("root",))
    handler = _handler(session_factory, executor)
    await _run(handler, job_type="account.gather", payload={"account_id": 2}, message_id="g-1")
    async with session_factory() as session:
        risks = (await session.execute(select(AccountRisk))).scalars().all()
        types = {(risk.username, risk.risk_type) for risk in risks}
        assert ("root", "privileged") in types
        assert ("deploy", "zombie") in types


@pytest.mark.asyncio
async def test_handler_rejects_cross_tenant_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    handler = _handler(session_factory, RecordingExecutor())
    with pytest.raises(ValueError, match="ACCOUNT_NOT_FOUND"):
        bound = bind_account_job_handler(handler, "account.verify")
        await bound(
            tenant_id="tenant-b",
            requested_by="user-1",
            payload={"account_id": 1},
            message_id="x-1",
        )
    async with session_factory() as session:
        run = (await session.execute(select(AccountAutomationRun))).scalar_one()
        assert run.status == "failed"
        assert run.error_code == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_verify_account_records_completed_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_inventory(session_factory)
    handler = _handler(session_factory, RecordingExecutor())
    await _run(handler, job_type="account.verify", payload={"account_id": 1}, message_id="v-1")
    async with session_factory() as session:
        run = (await session.execute(select(AccountAutomationRun))).scalar_one()
        assert run.status == "completed"
        assert run.result_summary == "account_verified"
