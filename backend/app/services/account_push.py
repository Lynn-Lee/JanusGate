"""#t73 account.push automation: Vault unwrap → SSH/SFTP authorized_keys upsert (mockable)."""
from __future__ import annotations

import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.host_key_trust import HostKeyTrustStore
from app.connectors.ssh_channel import SshChannel, SshCredential, SshTarget
from app.models.account import Account, AccountRisk
from app.models.asset import Asset
from app.models.automation import AutomationJobRun
from app.services.automation_worker import JsonValue

PUSH_FAILED_COPY = "推送失败"
PUSH_DENIED_COPY = "无法推送"
RISK_TYPE_PUSH_FAILED = "push_failed"
JOB_TYPE = "account.push"


@dataclass(frozen=True)
class AccountPushTarget:
    account_id: int
    tenant_id: str
    username: str
    protocol: str
    secret_id: str
    host: str
    port: int
    asset_id: int
    asset_name: str


class SecretUnwrapper(Protocol):
    def unwrap(self, secret_id: str) -> Awaitable[str] | str: ...


class HostKeyApprovalLookup(Protocol):
    async def approved_public_key(self, *, tenant_id: str, asset_id: str | int) -> str: ...


class AuthorizedKeysPusher(Protocol):
    """Write account public key into remote ~/.ssh/authorized_keys (SFTP)."""

    def push(
        self,
        *,
        target: AccountPushTarget,
        private_key_plaintext: str,
        public_key_line: str,
        trusted_host_key: str,
    ) -> Awaitable[None]: ...


class AlwaysFailAuthorizedKeysPusher:
    """默认 fail-closed 占位：未注入真实 pusher 时推送失败。"""

    async def push(
        self,
        *,
        target: AccountPushTarget,
        private_key_plaintext: str,
        public_key_line: str,
        trusted_host_key: str,
    ) -> None:
        del target, private_key_plaintext, public_key_line, trusted_host_key
        raise ValueError("SSH_PUSH_FAILED")


class HostKeyTrustApprovalLookup:
    """从 HostKeyTrustStore 读取已批准主机公钥；未批准返回空串。"""

    def __init__(self, store: HostKeyTrustStore) -> None:
        self._store = store

    async def approved_public_key(self, *, tenant_id: str, asset_id: str | int) -> str:
        row = await self._store.get(tenant_id=tenant_id, asset_id=str(asset_id))
        if row is None:
            return ""
        return (row.approved_public_key or "").strip()


def is_private_key_secret(plaintext: str) -> bool:
    """与 asset_vault_resolver._credential_from_plaintext 对齐：私钥 PEM/OpenSSH 视为 key-type。"""
    stripped = plaintext.strip()
    return stripped.startswith("-----BEGIN") or "OPENSSH PRIVATE KEY" in stripped


def public_key_line_from_private_key(private_key_plaintext: str) -> str:
    try:
        key = asyncssh.import_private_key(private_key_plaintext)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        raise ValueError("SSH_PUBLIC_KEY_DERIVE_FAILED") from exc
    exported = key.export_public_key()
    line = exported.decode() if isinstance(exported, bytes) else str(exported)
    return line.strip()


def upsert_authorized_keys_content(existing: str, public_key_line: str) -> str:
    """Append/upsert a public key line without wiping unrelated keys."""

    normalized = _normalize_pubkey_line(public_key_line)
    if not normalized:
        raise ValueError("SSH_PUBLIC_KEY_INVALID")
    match_token = _pubkey_match_token(normalized)
    kept: list[str] = []
    replaced = False
    for raw_line in existing.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(raw_line.rstrip("\n"))
            continue
        if _pubkey_match_token(stripped) == match_token:
            if not replaced:
                kept.append(normalized)
                replaced = True
            continue
        kept.append(raw_line.rstrip("\n"))
    if not replaced:
        if kept and kept[-1] != "":
            kept.append(normalized)
        else:
            kept.append(normalized)
    body = "\n".join(kept).rstrip() + "\n"
    return body


def _normalize_pubkey_line(line: str) -> str:
    parts = line.split()
    if len(parts) < 2:
        return ""
    # Keep type + key body; drop optional comment for stable identity, re-attach if present.
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1]} {' '.join(parts[2:])}".strip()
    return f"{parts[0]} {parts[1]}"


def _pubkey_match_token(line: str) -> str:
    parts = line.split()
    if len(parts) < 2:
        return line.strip()
    return f"{parts[0]} {parts[1]}"


class SftpAuthorizedKeysPusher:
    """使用现有 SSH/SFTP 栈写入 authorized_keys（.ssh 700 / authorized_keys 600）。"""

    async def push(
        self,
        *,
        target: AccountPushTarget,
        private_key_plaintext: str,
        public_key_line: str,
        trusted_host_key: str,
    ) -> None:
        ssh_target = SshTarget(
            host=target.host,
            port=target.port,
            username=target.username,
            trusted_host_key=trusted_host_key,
        )
        credential = SshCredential(private_key=private_key_plaintext)
        channel = await SshChannel.open(ssh_target, credential)
        try:
            sftp = await channel.start_sftp()
            try:
                await _ensure_ssh_dir(sftp)
                remote_path = ".ssh/authorized_keys"
                existing = await _read_text_if_exists(sftp, remote_path)
                payload = upsert_authorized_keys_content(existing, public_key_line).encode("utf-8")
                async with sftp.open(remote_path, "wb") as handle:
                    await handle.write(payload)
                await sftp.chmod(remote_path, 0o600)
            finally:
                with_context = getattr(sftp, "exit", None)
                if callable(with_context):
                    with contextlib.suppress(Exception):
                        sftp.exit()
        finally:
            await channel.close()


async def _ensure_ssh_dir(sftp: asyncssh.SFTPClient) -> None:
    try:
        await sftp.stat(".ssh")
    except (asyncssh.SFTPError, OSError, FileNotFoundError):
        # 可能已被并发创建；继续 chmod
        with contextlib.suppress(asyncssh.SFTPError, OSError):
            await sftp.mkdir(".ssh")
    await sftp.chmod(".ssh", 0o700)


async def _read_text_if_exists(sftp: asyncssh.SFTPClient, remote_path: str) -> str:
    try:
        async with sftp.open(remote_path, "rb") as handle:
            data = await handle.read()
    except (asyncssh.SFTPError, OSError, FileNotFoundError):
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


class AccountPushWorkerHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: SecretUnwrapper,
        host_keys: HostKeyApprovalLookup,
        pusher: AuthorizedKeysPusher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._host_keys = host_keys
        self._pusher = pusher or AlwaysFailAuthorizedKeysPusher()

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        account_id = _payload_int(payload, "account_id")

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
                    reason=PUSH_FAILED_COPY,
                    summary=None,
                )
                await session.commit()
                raise ValueError("ACCOUNT_NOT_FOUND")

            target = AccountPushTarget(
                account_id=account.id,
                tenant_id=account.tenant_id,
                username=account.username,
                protocol=account.protocol,
                secret_id=account.secret_id,
                host=asset.address,
                port=int(asset.port or 22),
                asset_id=asset.id,
                asset_name=asset.name,
            )
            summary = _summary(target.username, target.asset_name, "进行中")

            if account.protocol.lower() != "ssh":
                await _fail_closed(
                    session,
                    account=account,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    error_code="ACCOUNT_PUSH_SSH_ONLY",
                    reason=PUSH_DENIED_COPY,
                    summary=_summary(target.username, target.asset_name, "失败"),
                )
                raise ValueError("ACCOUNT_PUSH_SSH_ONLY")

            if str(getattr(account, "credential_type", "") or "").lower() == "password":
                await _fail_closed(
                    session,
                    account=account,
                    message_id=message_id,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    error_code="ACCOUNT_PUSH_KEY_ONLY",
                    reason=PUSH_DENIED_COPY,
                    summary=_summary(target.username, target.asset_name, "失败"),
                )
                raise ValueError("ACCOUNT_PUSH_KEY_ONLY")

            await _record_run(
                session,
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                status="running",
                error_code=None,
                reason=None,
                summary=summary,
            )
            await session.commit()

        try:
            trusted = await self._host_keys.approved_public_key(
                tenant_id=tenant_id, asset_id=target.asset_id
            )
            if not trusted:
                raise ValueError("HOST_KEY_UNAPPROVED")

            plaintext = await _unwrap(self._secrets, target.secret_id)
            if not is_private_key_secret(plaintext):
                raise ValueError("ACCOUNT_PUSH_KEY_ONLY")

            public_key_line = public_key_line_from_private_key(plaintext)
            await self._pusher.push(
                target=target,
                private_key_plaintext=plaintext,
                public_key_line=public_key_line,
                trusted_host_key=trusted,
            )
        except Exception as exc:
            reason = (
                PUSH_DENIED_COPY
                if _is_push_denied(exc)
                else PUSH_FAILED_COPY
            )
            async with self._session_factory() as session:
                account = await session.get(Account, account_id)
                session.add(
                    AccountRisk(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        risk_type=RISK_TYPE_PUSH_FAILED,
                        reason=reason,
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
                    reason=reason,
                    summary=_summary(target.username, target.asset_name, "失败"),
                )
                if account is not None:
                    account.updated_at = datetime.now(UTC)
                await session.commit()
            raise ValueError("ACCOUNT_PUSH_FAILED") from exc

        async with self._session_factory() as session:
            await _record_run(
                session,
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                status="completed",
                error_code=None,
                reason=None,
                summary=_summary(target.username, target.asset_name, "成功"),
            )
            await session.commit()


def _is_push_denied(exc: Exception) -> bool:
    code = ""
    if exc.args and isinstance(exc.args[0], str):
        code = exc.args[0]
    return code in {"HOST_KEY_UNAPPROVED", "ACCOUNT_PUSH_KEY_ONLY", "ACCOUNT_PUSH_SSH_ONLY"}


def _summary(username: str, asset_name: str, result: str) -> str:
    return f"{username} · {asset_name} · {result}"


async def _fail_closed(
    session: AsyncSession,
    *,
    account: Account,
    message_id: str,
    tenant_id: str,
    requested_by: str,
    error_code: str,
    reason: str,
    summary: str,
) -> None:
    session.add(
        AccountRisk(
            tenant_id=tenant_id,
            account_id=account.id,
            risk_type=RISK_TYPE_PUSH_FAILED,
            reason=reason,
            job_message_id=message_id,
        )
    )
    await _record_run(
        session,
        message_id=message_id,
        tenant_id=tenant_id,
        requested_by=requested_by,
        status="failed",
        error_code=error_code,
        reason=reason,
        summary=summary,
    )
    await session.commit()


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


async def _record_run(
    session: AsyncSession,
    *,
    message_id: str,
    tenant_id: str,
    requested_by: str,
    status: str,
    error_code: str | None,
    reason: str | None,
    summary: str | None,
) -> None:
    run = await session.get(AutomationJobRun, message_id)
    if run is None:
        run = AutomationJobRun(
            message_id=message_id,
            tenant_id=tenant_id,
            job_type=JOB_TYPE,
            requested_by=requested_by,
        )
        session.add(run)
    run.status = status
    run.error_code = error_code
    # reason = 用户文案；summary 复用 playbook_name 字段展示「账号 · 资产 · 结果」（无凭据）
    run.reason = reason
    if summary is not None:
        run.playbook_name = summary


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
