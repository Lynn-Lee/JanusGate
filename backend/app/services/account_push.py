"""#t73 account.push automation: Vault unwrap → SSH public-key push (mockable).

载荷仅 ``account_id`` + ``privileged_account_id``（同资产特权 SSH 账号），
凭据只在 worker 侧 Vault unwrap 后出现，绝不进队列 / argv / 临时文件。
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.account import Account, AccountRisk
from app.models.asset import Asset
from app.models.automation import AutomationJobRun
from app.services.automation_worker import JsonValue

PUSH_FAILED_COPY = "推送失败"
PUSH_STATUS_UNPUSHED = "unpushed"
PUSH_STATUS_PUSHING = "pushing"
PUSH_STATUS_SUCCESS = "success"
PUSH_STATUS_FAILED = "failed"
RISK_TYPE_PUSH_FAILED = "push_failed"

_PUBLIC_KEY_PREFIXES = (
    "ssh-ed25519 ",
    "ssh-rsa ",
    "ecdsa-sha2-nistp256 ",
    "ecdsa-sha2-nistp384 ",
    "ecdsa-sha2-nistp521 ",
    "ssh-ed448 ",
)


@dataclass(frozen=True)
class AccountPushTarget:
    account_id: int
    tenant_id: str
    username: str
    host: str
    port: int
    public_key: str


@dataclass(frozen=True)
class PrivilegedSshCredential:
    username: str
    password: str | None = None
    private_key: str | None = None


class SecretUnwrapper(Protocol):
    def unwrap(self, secret_id: str) -> Awaitable[str] | str: ...


class SshAccountPusher(Protocol):
    """把目标账号公钥写入远端 ``authorized_keys``；测试可注入失败/成功实现。"""

    def push(
        self,
        *,
        target: AccountPushTarget,
        privileged: PrivilegedSshCredential,
    ) -> Awaitable[None]: ...


class AlwaysFailSshAccountPusher:
    """默认 fail-closed 占位：未注入真实 pusher 时推送失败。"""

    async def push(
        self,
        *,
        target: AccountPushTarget,
        privileged: PrivilegedSshCredential,
    ) -> None:
        del target, privileged
        raise ValueError("SSH_PUSH_PROBE_FAILED")


class AccountPushWorkerHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: SecretUnwrapper,
        pusher: SshAccountPusher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._pusher = pusher or AlwaysFailSshAccountPusher()

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        account_id = _payload_int(payload, "account_id")
        privileged_account_id = _payload_int(payload, "privileged_account_id")

        async with self._session_factory() as session:
            account, asset = await _load_account_asset(
                session, tenant_id=tenant_id, account_id=account_id
            )
            privileged = await _load_account(
                session, tenant_id=tenant_id, account_id=privileged_account_id
            )
            if account is None or asset is None or privileged is None:
                await _record_run(
                    session,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    status="failed",
                    error_code="ACCOUNT_NOT_FOUND",
                    reason=PUSH_FAILED_COPY,
                )
                await session.commit()
                raise ValueError("ACCOUNT_NOT_FOUND")

            error_code = _validate_push_pair(account=account, privileged=privileged)
            if error_code is not None:
                account.push_status = PUSH_STATUS_FAILED
                account.last_push_message_id = message_id
                account.last_push_at = datetime.now(UTC)
                await _record_run(
                    session,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    status="failed",
                    error_code=error_code,
                    reason=PUSH_FAILED_COPY,
                )
                session.add(
                    AccountRisk(
                        tenant_id=tenant_id,
                        account_id=account.id,
                        risk_type=RISK_TYPE_PUSH_FAILED,
                        reason=PUSH_FAILED_COPY,
                        job_message_id=message_id,
                    )
                )
                await session.commit()
                raise ValueError(error_code)

            target_secret_id = account.secret_id
            privileged_secret_id = privileged.secret_id
            privileged_username = privileged.username
            target = AccountPushTarget(
                account_id=account.id,
                tenant_id=account.tenant_id,
                username=account.username,
                host=asset.address,
                port=int(asset.port or 22),
                public_key="",  # filled after unwrap
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
            target_secret = await _unwrap(self._secrets, target_secret_id)
            privileged_secret = await _unwrap(self._secrets, privileged_secret_id)
            public_key = extract_openssh_public_key(target_secret)
            privileged_cred = _privileged_credential(
                username=privileged_username, secret_plaintext=privileged_secret
            )
            await self._pusher.push(
                target=AccountPushTarget(
                    account_id=target.account_id,
                    tenant_id=target.tenant_id,
                    username=target.username,
                    host=target.host,
                    port=target.port,
                    public_key=public_key,
                ),
                privileged=privileged_cred,
            )
        except Exception as exc:
            async with self._session_factory() as session:
                account = await session.get(Account, account_id)
                if account is not None:
                    account.push_status = PUSH_STATUS_FAILED
                    account.last_push_message_id = message_id
                    account.last_push_at = datetime.now(UTC)
                session.add(
                    AccountRisk(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        risk_type=RISK_TYPE_PUSH_FAILED,
                        reason=PUSH_FAILED_COPY,
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
                    reason=PUSH_FAILED_COPY,
                )
                await session.commit()
            raise ValueError("ACCOUNT_PUSH_FAILED") from exc

        async with self._session_factory() as session:
            account = await session.get(Account, account_id)
            if account is not None:
                account.push_status = PUSH_STATUS_SUCCESS
                account.last_push_message_id = message_id
                account.last_push_at = datetime.now(UTC)
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


def extract_openssh_public_key(secret_plaintext: str) -> str:
    """从 Vault 明文解析 OpenSSH 公钥行；私钥 PEM 则导出对应公钥。"""

    text = secret_plaintext.strip()
    if not text:
        raise ValueError("ACCOUNT_PUSH_PUBLIC_KEY_INVALID")
    first_line = text.splitlines()[0].strip()
    if first_line.startswith(_PUBLIC_KEY_PREFIXES):
        return first_line
    try:
        key = asyncssh.import_private_key(text)
        exported = key.export_public_key().decode("utf-8").strip()
    except (asyncssh.KeyImportError, UnicodeError, AttributeError, TypeError) as exc:
        raise ValueError("ACCOUNT_PUSH_PUBLIC_KEY_INVALID") from exc
    if not exported.startswith(_PUBLIC_KEY_PREFIXES):
        raise ValueError("ACCOUNT_PUSH_PUBLIC_KEY_INVALID")
    return exported.splitlines()[0].strip()


def _privileged_credential(*, username: str, secret_plaintext: str) -> PrivilegedSshCredential:
    text = secret_plaintext.strip()
    if text.startswith("-----BEGIN"):
        return PrivilegedSshCredential(username=username, private_key=text)
    return PrivilegedSshCredential(username=username, password=text)


def _validate_push_pair(*, account: Account, privileged: Account) -> str | None:
    if account.protocol.lower() != "ssh" or privileged.protocol.lower() != "ssh":
        return "ACCOUNT_PUSH_SSH_ONLY"
    if account.asset_id != privileged.asset_id:
        return "ACCOUNT_PUSH_PRIVILEGED_ASSET_MISMATCH"
    if account.id == privileged.id:
        return "ACCOUNT_PUSH_PRIVILEGED_SAME_ACCOUNT"
    if privileged.status != "active":
        return "ACCOUNT_PUSH_PRIVILEGED_INACTIVE"
    return None


async def _unwrap(secrets: SecretUnwrapper, secret_id: str) -> str:
    value = secrets.unwrap(secret_id)
    if hasattr(value, "__await__"):
        return await value
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


async def _load_account(
    session: AsyncSession, *, tenant_id: str, account_id: int
) -> Account | None:
    result = await session.execute(
        select(Account).where(Account.id == account_id).where(Account.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


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
            job_type="account.push",
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
