"""#t73 account.verify automation: Vault unwrap → SSH probe (mockable)."""
from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.account import Account, AccountRisk
from app.models.asset import Asset
from app.models.automation import AutomationJobRun
from app.services.automation_worker import JsonValue

VERIFY_FAILED_COPY = "校验失败"
VERIFY_STATUS_UNVERIFIED = "unverified"
VERIFY_STATUS_VERIFYING = "verifying"
VERIFY_STATUS_SUCCESS = "success"
VERIFY_STATUS_FAILED = "failed"
RISK_TYPE_VERIFY_FAILED = "verify_failed"


@dataclass(frozen=True)
class AccountVerifyTarget:
    account_id: int
    tenant_id: str
    username: str
    protocol: str
    secret_id: str
    host: str
    port: int


class SecretUnwrapper(Protocol):
    def unwrap(self, secret_id: str) -> Awaitable[str] | str: ...


class SshAccountProbe(Protocol):
    """SSH 连通性探测；测试可注入失败/成功实现，生产可接 asyncssh。"""

    def probe(
        self,
        *,
        target: AccountVerifyTarget,
        secret_plaintext: str,
    ) -> Awaitable[None]: ...


class AlwaysFailSshAccountProbe:
    """默认 fail-closed 占位：未注入真实 probe 时校验失败（不抛栈到用户）。"""

    async def probe(self, *, target: AccountVerifyTarget, secret_plaintext: str) -> None:
        del target, secret_plaintext
        raise ValueError("SSH_VERIFY_PROBE_FAILED")


class AccountVerifyWorkerHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: SecretUnwrapper,
        probe: SshAccountProbe | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._probe = probe or AlwaysFailSshAccountProbe()

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        account_id = _payload_int(payload, "account_id")
        # 载荷禁止凭据：仅 account_id；额外敏感键由 enqueue 侧白名单拦截。

        async with self._session_factory() as session:
            account, asset = await _load_account_asset(
                session, tenant_id=tenant_id, account_id=account_id
            )
            if account is None or asset is None:
                await _record_run(
                    session,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    status="failed",
                    error_code="ACCOUNT_NOT_FOUND",
                    reason=VERIFY_FAILED_COPY,
                )
                await session.commit()
                raise ValueError("ACCOUNT_NOT_FOUND")
            if account.protocol.lower() != "ssh":
                account.verify_status = VERIFY_STATUS_FAILED
                account.last_verify_message_id = message_id
                account.last_verify_at = datetime.now(UTC)
                await _record_run(
                    session,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    status="failed",
                    error_code="ACCOUNT_VERIFY_SSH_ONLY",
                    reason=VERIFY_FAILED_COPY,
                )
                session.add(
                    AccountRisk(
                        tenant_id=tenant_id,
                        account_id=account.id,
                        risk_type=RISK_TYPE_VERIFY_FAILED,
                        reason=VERIFY_FAILED_COPY,
                        job_message_id=message_id,
                    )
                )
                await session.commit()
                raise ValueError("ACCOUNT_VERIFY_SSH_ONLY")

            target = AccountVerifyTarget(
                account_id=account.id,
                tenant_id=account.tenant_id,
                username=account.username,
                protocol=account.protocol,
                secret_id=account.secret_id,
                host=asset.address,
                port=int(asset.port or 22),
            )
            await _record_run(
                session,
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                status="running",
                error_code=None,
                reason=None,
            )
            await session.commit()

        try:
            plaintext = await _unwrap(self._secrets, target.secret_id)
            await self._probe.probe(target=target, secret_plaintext=plaintext)
        except Exception as exc:
            async with self._session_factory() as session:
                account = await session.get(Account, account_id)
                if account is not None:
                    account.verify_status = VERIFY_STATUS_FAILED
                    account.last_verify_message_id = message_id
                    account.last_verify_at = datetime.now(UTC)
                session.add(
                    AccountRisk(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        risk_type=RISK_TYPE_VERIFY_FAILED,
                        reason=VERIFY_FAILED_COPY,
                        job_message_id=message_id,
                    )
                )
                await _record_run(
                    session,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    status="failed",
                    error_code=_safe_error_code(exc),
                    reason=VERIFY_FAILED_COPY,
                )
                await session.commit()
            # 不向上冒泡堆栈给调用方用户文案；worker 可选择再抛。
            raise ValueError("ACCOUNT_VERIFY_FAILED") from exc

        async with self._session_factory() as session:
            account = await session.get(Account, account_id)
            if account is not None:
                account.verify_status = VERIFY_STATUS_SUCCESS
                account.last_verify_message_id = message_id
                account.last_verify_at = datetime.now(UTC)
            await _record_run(
                session,
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                status="completed",
                error_code=None,
                reason=None,
            )
            await session.commit()


async def _unwrap(secrets: SecretUnwrapper, secret_id: str) -> str:
    value = secrets.unwrap(secret_id)
    if hasattr(value, "__await__"):
        return await value  # type: ignore[misc]
    return str(value)


async def _load_account_asset(
    session: AsyncSession, *, tenant_id: str, account_id: int
) -> tuple[Account | None, Asset | None]:
    result = await session.execute(
        select(Account, Asset)
        .join(Asset, Account.asset_id == Asset.id)
        .where(Account.id == account_id)
        .where(Account.tenant_id == tenant_id)
    )
    row = result.one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]


async def _record_run(
    session: AsyncSession,
    *,
    message_id: str,
    tenant_id: str,
    requested_by: str,
    status: str,
    error_code: str | None,
    reason: str | None,
) -> None:
    run = await session.get(AutomationJobRun, message_id)
    if run is None:
        run = AutomationJobRun(
            message_id=message_id,
            tenant_id=tenant_id,
            job_type="account.verify",
            requested_by=requested_by,
        )
        session.add(run)
    run.status = status
    run.error_code = error_code
    run.reason = reason


def _payload_int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _safe_error_code(exc: Exception) -> str:
    if exc.args and isinstance(exc.args[0], str) and exc.args[0]:
        return str(exc.args[0])[:120]
    message = str(exc).strip()
    if message:
        return message[:120]
    return exc.__class__.__name__[:120]
