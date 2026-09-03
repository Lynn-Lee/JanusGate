"""账号自动化执行器与队列 handler（#t73）。

八类自动化对标 JumpServer 语义（行为对标，非代码复制）：

- ``account.push`` / ``push_account``
- ``account.change_secret`` / ``change_secret``（亦作为生产 ``CredentialRotator``）
- ``account.verify`` / ``verify_account``
- ``account.remove`` / ``remove_account``
- ``account.gather`` / ``gather_accounts``
- ``account.verify_gateway`` / ``verify_gateway_account``
- ``account.check`` / ``check_account``
- ``account.backup`` / ``backup_account``

安全约束：

- 改密经 ``asyncssh`` 的 stdin 传递 ``chpasswd`` 输入，**不**把密码拼进命令行、argv 或
  shell；不使用 ``sshpass``（关闭 P0#16）。
- 结构化日志经 :mod:`app.core.logging` 脱敏，禁止 ``print()``（关闭 P2#13）。
- 队列 payload 只携带 account/asset/template id 与 reason，不含明文。
"""

from __future__ import annotations

import json
import secrets
import string
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.ssh_channel import (
    MODERN_ENCRYPTION_ALGS,
    MODERN_HOST_KEY_ALGS,
    MODERN_KEX_ALGS,
    MODERN_MAC_ALGS,
    SshChannelError,
    SshCredential,
    SshTarget,
)
from app.core.logging import get_logger
from app.core.security import password_policy_violations
from app.models.account import (
    Account,
    AccountAutomationRun,
    AccountBackup,
    AccountRisk,
    AccountTemplate,
    CredentialRotation,
)
from app.models.asset import Asset
from app.services.automation_worker import JsonValue
from app.services.credential_rotation import CredentialRotationResult

logger = get_logger("account_automation")

ACCOUNT_JOB_TYPES: frozenset[str] = frozenset(
    {
        "account.push",
        "account.change_secret",
        "account.verify",
        "account.remove",
        "account.gather",
        "account.verify_gateway",
        "account.check",
        "account.backup",
    }
)

PRIVILEGED_USERNAMES: frozenset[str] = frozenset({"root", "Administrator", "admin"})


@dataclass(frozen=True)
class AccountAutomationTarget:
    """执行器可见的账号/资产摘要，不含凭据明文。"""

    account_id: int | None
    asset_id: int
    tenant_id: str
    username: str
    protocol: str
    address: str
    port: int
    secret_id: str | None = None
    privileged: bool = False
    shell: str | None = None
    home_dir: str | None = None
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountAutomationResult:
    """执行结果摘要；``summary`` 不得包含密码/私钥。"""

    summary: str
    risks: tuple[AccountRiskDraft, ...] = ()
    new_secret_plaintext: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class AccountRiskDraft:
    username: str
    risk_type: str
    severity: str
    detail: str
    asset_id: int | None = None
    account_id: int | None = None


@dataclass(frozen=True)
class DiscoveredAccount:
    username: str
    uid: int | None
    home_dir: str | None
    shell: str | None


class SecretMaterialStore(Protocol):
    """Vault 侧最小契约：unwrap / rotate / create。"""

    async def unwrap(self, secret_id: str) -> str: ...

    async def rotate(self, secret_id: str, new_plaintext: str) -> object: ...

    async def create_secret(self, name: str, plaintext: str) -> object: ...


class AccountAutomationExecutor(Protocol):
    """八类账号自动化的远程/本地执行契约。"""

    async def push_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult: ...

    async def change_secret(
        self, target: AccountAutomationTarget, *, new_password: str
    ) -> AccountAutomationResult: ...

    async def verify_account(self, target: AccountAutomationTarget) -> AccountAutomationResult: ...

    async def remove_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult: ...

    async def gather_accounts(
        self, target: AccountAutomationTarget
    ) -> tuple[AccountAutomationResult, list[DiscoveredAccount]]: ...

    async def verify_gateway_account(
        self, target: AccountAutomationTarget
    ) -> AccountAutomationResult: ...

    async def check_account(
        self, target: AccountAutomationTarget, *, plaintext: str | None
    ) -> AccountAutomationResult: ...

    async def backup_account(self, target: AccountAutomationTarget) -> AccountAutomationResult: ...


class LocalPolicyAccountExecutor:
    """不依赖远程主机的本地策略执行器（check / backup / gateway 本地校验）。

    远程类操作默认抛出 ``ACCOUNT_EXECUTOR_REMOTE_REQUIRED``，由
    :class:`SshAccountAutomationExecutor` 覆盖。
    """

    async def push_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult:
        del target, admin
        raise ValueError("ACCOUNT_EXECUTOR_REMOTE_REQUIRED")

    async def change_secret(
        self, target: AccountAutomationTarget, *, new_password: str
    ) -> AccountAutomationResult:
        del target, new_password
        raise ValueError("ACCOUNT_EXECUTOR_REMOTE_REQUIRED")

    async def verify_account(self, target: AccountAutomationTarget) -> AccountAutomationResult:
        del target
        raise ValueError("ACCOUNT_EXECUTOR_REMOTE_REQUIRED")

    async def remove_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult:
        del target, admin
        raise ValueError("ACCOUNT_EXECUTOR_REMOTE_REQUIRED")

    async def gather_accounts(
        self, target: AccountAutomationTarget
    ) -> tuple[AccountAutomationResult, list[DiscoveredAccount]]:
        del target
        raise ValueError("ACCOUNT_EXECUTOR_REMOTE_REQUIRED")

    async def verify_gateway_account(
        self, target: AccountAutomationTarget
    ) -> AccountAutomationResult:
        """网关账号校验：#t67 网域未落地前，仅校验托管账号记录可用。"""

        if target.secret_id is None or target.account_id is None:
            raise ValueError("GATEWAY_ACCOUNT_NOT_FOUND")
        logger.info(
            "verify_gateway_account",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
        )
        return AccountAutomationResult(summary="gateway_account_present")

    async def check_account(
        self, target: AccountAutomationTarget, *, plaintext: str | None
    ) -> AccountAutomationResult:
        risks: list[AccountRiskDraft] = []
        if target.username in PRIVILEGED_USERNAMES or target.privileged:
            risks.append(
                AccountRiskDraft(
                    username=target.username,
                    risk_type="privileged",
                    severity="high",
                    detail="privileged account detected",
                    asset_id=target.asset_id,
                    account_id=target.account_id,
                )
            )
        if plaintext is not None:
            violations = password_policy_violations(plaintext)
            if violations:
                risks.append(
                    AccountRiskDraft(
                        username=target.username,
                        risk_type="weak_password",
                        severity="high",
                        detail="; ".join(violations)[:480],
                        asset_id=target.asset_id,
                        account_id=target.account_id,
                    )
                )
        logger.info(
            "check_account",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
            risk_count=len(risks),
        )
        return AccountAutomationResult(
            summary=f"checked risks={len(risks)}",
            risks=tuple(risks),
        )

    async def backup_account(self, target: AccountAutomationTarget) -> AccountAutomationResult:
        if target.account_id is None or target.secret_id is None:
            raise ValueError("ACCOUNT_NOT_FOUND")
        logger.info(
            "backup_account",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
        )
        return AccountAutomationResult(summary="account_metadata_backed_up")


class SshAccountAutomationExecutor(LocalPolicyAccountExecutor):
    """基于 asyncssh 的远程账号自动化执行器。

    密码仅经 ``connection.run(..., input=...)`` 的 stdin 传递给 ``chpasswd``，
    命令字符串本身永不包含密码。
    """

    def __init__(
        self,
        *,
        secrets_store: SecretMaterialStore,
        connect_timeout: float = 10.0,
    ) -> None:
        self._secrets = secrets_store
        self._connect_timeout = connect_timeout

    async def push_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult:
        new_password = _generate_password()
        async with await self._open(admin) as conn:
            create_cmd = _useradd_command(target)
            result = await conn.run(create_cmd, check=False)
            if result.exit_status not in (0, None) and result.exit_status != 9:
                raise ValueError(_remote_error("ACCOUNT_PUSH_FAILED", result))
            await _chpasswd(conn, username=target.username, password=new_password)
        logger.info(
            "push_account",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
        )
        return AccountAutomationResult(
            summary=f"pushed user={target.username}",
            new_secret_plaintext=new_password,
        )

    async def change_secret(
        self, target: AccountAutomationTarget, *, new_password: str
    ) -> AccountAutomationResult:
        if not new_password:
            raise ValueError("NEW_PASSWORD_REQUIRED")
        # 用当前凭据登录后，经 stdin 改密；命令行无密码。
        async with await self._open(target) as conn:
            await _chpasswd(conn, username=target.username, password=new_password)
        logger.info(
            "change_secret",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
        )
        return AccountAutomationResult(summary="secret_changed")

    async def verify_account(self, target: AccountAutomationTarget) -> AccountAutomationResult:
        async with await self._open(target) as conn:
            result = await conn.run("true", check=False)
            if result.exit_status not in (0, None):
                raise ValueError("ACCOUNT_VERIFY_FAILED")
        logger.info(
            "verify_account",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
        )
        return AccountAutomationResult(summary="account_verified")

    async def remove_account(
        self, target: AccountAutomationTarget, *, admin: AccountAutomationTarget
    ) -> AccountAutomationResult:
        async with await self._open(admin) as conn:
            result = await conn.run(f"userdel -r -- {target.username}", check=False)
            if result.exit_status not in (0, None) and result.exit_status != 6:
                raise ValueError(_remote_error("ACCOUNT_REMOVE_FAILED", result))
        logger.info(
            "remove_account",
            account_id=target.account_id,
            asset_id=target.asset_id,
            username=target.username,
        )
        return AccountAutomationResult(summary=f"removed user={target.username}")

    async def gather_accounts(
        self, target: AccountAutomationTarget
    ) -> tuple[AccountAutomationResult, list[DiscoveredAccount]]:
        async with await self._open(target) as conn:
            result = await conn.run("getent passwd", check=False)
            if result.exit_status not in (0, None):
                raise ValueError(_remote_error("ACCOUNT_GATHER_FAILED", result))
            stdout = _decode(result.stdout)
        discovered = _parse_passwd(stdout)
        risks = tuple(
            AccountRiskDraft(
                username=item.username,
                risk_type="privileged",
                severity="high",
                detail="privileged account discovered on host",
                asset_id=target.asset_id,
            )
            for item in discovered
            if item.username in PRIVILEGED_USERNAMES or (item.uid is not None and item.uid == 0)
        )
        logger.info(
            "gather_accounts",
            asset_id=target.asset_id,
            discovered=len(discovered),
            privileged=len(risks),
        )
        return (
            AccountAutomationResult(
                summary=f"gathered accounts={len(discovered)}",
                risks=risks,
            ),
            discovered,
        )

    async def _open(self, target: AccountAutomationTarget) -> asyncssh.SSHClientConnection:
        if target.secret_id is None:
            raise ValueError("SECRET_NOT_FOUND")
        plaintext = await self._secrets.unwrap(target.secret_id)
        credential = _credential_from_plaintext(plaintext)
        # 账号自动化场景允许调用方注入 trusted_host_key；测试可用固定密钥。
        trusted = getattr(target, "trusted_host_key", None)
        if not isinstance(trusted, str) or not trusted.strip():
            raise ValueError("SSH_TRUSTED_HOST_KEY_MISSING")
        ssh_target = SshTarget(
            host=target.address,
            port=target.port,
            username=target.username,
            trusted_host_key=trusted,
        )
        return await _connect(ssh_target, credential, timeout=self._connect_timeout)


@dataclass(frozen=True)
class SshAutomationTarget(AccountAutomationTarget):
    """带可信主机公钥的远程目标。"""

    trusted_host_key: str = ""


class SshChangeSecretRotator:
    """生产 ``CredentialRotator``：远程改密 + Vault rotate。"""

    def __init__(
        self,
        *,
        secrets_store: SecretMaterialStore,
        trusted_host_key_resolver: TrustedHostKeyResolver,
        connect_timeout: float = 10.0,
    ) -> None:
        self._secrets = secrets_store
        self._host_keys = trusted_host_key_resolver
        self._executor = SshAccountAutomationExecutor(
            secrets_store=secrets_store, connect_timeout=connect_timeout
        )

    async def rotate(
        self, account: Account, rotation: CredentialRotation
    ) -> CredentialRotationResult:
        del rotation
        asset = account.asset
        if asset is None:
            raise ValueError("ASSET_NOT_FOUND")
        trusted = await self._host_keys.resolve(tenant_id=account.tenant_id, asset_id=asset.id)
        target = SshAutomationTarget(
            account_id=account.id,
            asset_id=asset.id,
            tenant_id=account.tenant_id,
            username=account.username,
            protocol=account.protocol,
            address=asset.address,
            port=asset.port,
            secret_id=account.secret_id,
            trusted_host_key=trusted,
        )
        new_password = _generate_password()
        await self._executor.change_secret(target, new_password=new_password)
        await self._secrets.rotate(account.secret_id, new_password)
        logger.info(
            "credential_rotated",
            account_id=account.id,
            asset_id=asset.id,
            username=account.username,
        )
        return CredentialRotationResult(secret_id=account.secret_id)


class TrustedHostKeyResolver(Protocol):
    async def resolve(self, *, tenant_id: str, asset_id: int) -> str: ...


class AccountAutomationWorkerHandler:
    """统一消费八类 ``account.*`` 队列消息。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        executor: AccountAutomationExecutor,
        secrets_store: SecretMaterialStore | None = None,
        trusted_host_key_resolver: TrustedHostKeyResolver | None = None,
        admin_account_resolver: AdminAccountResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._executor = executor
        self._secrets = secrets_store
        self._host_keys = trusted_host_key_resolver
        self._admin_resolver = admin_account_resolver

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        job_type = _payload_optional_str(payload, "job_type")
        # worker 分发时 job_type 在消息字段；payload 可冗余携带，便于单测直调。
        effective_type = job_type or ""
        if effective_type not in ACCOUNT_JOB_TYPES:
            # 由外层 AutomationWorker 按 handlers 键分发时，用包装器注入 job_type。
            raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")

        async with self._session_factory() as session:
            existing = await session.execute(
                select(AccountAutomationRun).where(AccountAutomationRun.message_id == message_id)
            )
            run = existing.scalar_one_or_none()
            if run is None:
                run = AccountAutomationRun(
                    message_id=message_id,
                    tenant_id=tenant_id,
                    job_type=effective_type,
                    status="running",
                    requested_by=requested_by,
                    account_id=_payload_optional_int(payload, "account_id"),
                    asset_id=_payload_optional_int(payload, "asset_id"),
                    template_id=_payload_optional_int(payload, "template_id"),
                )
                session.add(run)
            else:
                run.status = "running"
                run.error_code = None
                run.result_summary = None
            await session.flush()
            try:
                summary = await self._dispatch(
                    session,
                    job_type=effective_type,
                    tenant_id=tenant_id,
                    requested_by=requested_by,
                    payload=payload,
                    message_id=message_id,
                )
            except Exception as exc:
                run.status = "failed"
                run.error_code = _error_code(exc)
                await session.commit()
                logger.warning(
                    "account_automation_failed",
                    job_type=effective_type,
                    message_id=message_id,
                    error_code=run.error_code,
                )
                raise
            run.status = "completed"
            run.result_summary = summary[:480]
            run.error_code = None
            await session.commit()
            logger.info(
                "account_automation_completed",
                job_type=effective_type,
                message_id=message_id,
                summary=run.result_summary,
            )

    async def _dispatch(
        self,
        session: AsyncSession,
        *,
        job_type: str,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> str:
        if job_type == "account.gather":
            # 需一个可登录账号执行 getent；发现结果按该账号所属资产归属。
            account_id = _payload_int(payload, "account_id")
            target = await self._load_target(session, tenant_id=tenant_id, account_id=account_id)
            target = await self._with_host_key(target)
            result, discovered = await self._executor.gather_accounts(target)
            zombie_drafts = await _zombie_risks(
                session,
                tenant_id=tenant_id,
                asset_id=target.asset_id,
                discovered_usernames={item.username for item in discovered},
            )
            await _persist_risks(
                session,
                tenant_id=tenant_id,
                drafts=(*result.risks, *zombie_drafts),
                source_job_type=job_type,
                source_message_id=message_id,
            )
            return result.summary

        if job_type == "account.push":
            asset_id = _payload_int(payload, "asset_id")
            template_id = _payload_int(payload, "template_id")
            template = await _get_template(session, tenant_id=tenant_id, template_id=template_id)
            if template is None:
                raise ValueError("ACCOUNT_TEMPLATE_NOT_FOUND")
            admin = await self._require_admin_target(session, tenant_id=tenant_id, asset_id=asset_id)
            asset = await _get_active_asset(session, tenant_id=tenant_id, asset_id=asset_id)
            if asset is None:
                raise ValueError("ASSET_NOT_FOUND")
            target = AccountAutomationTarget(
                account_id=None,
                asset_id=asset.id,
                tenant_id=tenant_id,
                username=template.username,
                protocol=template.protocol,
                address=asset.address,
                port=asset.port,
                privileged=template.privileged,
                shell=template.shell,
                home_dir=template.home_dir,
                groups=tuple(_parse_groups(template.groups_json)),
            )
            if isinstance(admin, SshAutomationTarget) or self._host_keys is not None:
                target = await self._with_host_key(target)
                admin = await self._with_host_key(admin)
            result = await self._executor.push_account(target, admin=admin)
            if result.new_secret_plaintext is None or self._secrets is None:
                raise ValueError("ACCOUNT_PUSH_SECRET_MISSING")
            created = await self._secrets.create_secret(
                f"account:{tenant_id}:{asset.id}:{template.username}",
                result.new_secret_plaintext,
            )
            secret_id = _secret_id_of(created)
            account = Account(
                tenant_id=tenant_id,
                asset_id=asset.id,
                username=template.username,
                protocol=template.protocol,
                secret_id=secret_id,
                organization_id=template.organization_id,
                team_id=template.team_id,
                project_id=template.project_id,
                status="active",
            )
            session.add(account)
            await session.flush()
            return result.summary

        account_id = _payload_int(payload, "account_id")
        target = await self._load_target(session, tenant_id=tenant_id, account_id=account_id)

        if job_type == "account.change_secret":
            if self._secrets is None:
                raise ValueError("SECRET_STORE_REQUIRED")
            target = await self._with_host_key(target)
            new_password = _generate_password()
            await self._executor.change_secret(target, new_password=new_password)
            assert target.secret_id is not None
            await self._secrets.rotate(target.secret_id, new_password)
            managed = await _get_active_account(
                session, tenant_id=tenant_id, account_id=account_id
            )
            if managed is None:
                raise ValueError("ACCOUNT_NOT_FOUND")
            rotation = CredentialRotation(
                tenant_id=tenant_id,
                account_id=managed.id,
                status="completed",
                reason=_payload_optional_str(payload, "reason"),
                requested_by=requested_by,
                previous_secret_id=managed.secret_id,
                new_secret_id=managed.secret_id,
            )
            session.add(rotation)
            return "secret_changed"

        if job_type == "account.verify":
            target = await self._with_host_key(target)
            result = await self._executor.verify_account(target)
            return result.summary

        if job_type == "account.remove":
            admin = await self._require_admin_target(
                session, tenant_id=tenant_id, asset_id=target.asset_id
            )
            target = await self._with_host_key(target)
            admin = await self._with_host_key(admin)
            result = await self._executor.remove_account(target, admin=admin)
            managed = await _get_active_account(
                session, tenant_id=tenant_id, account_id=account_id
            )
            if managed is not None:
                managed.status = "removed"
            return result.summary

        if job_type == "account.verify_gateway":
            result = await self._executor.verify_gateway_account(target)
            return result.summary

        if job_type == "account.check":
            plaintext: str | None = None
            if self._secrets is not None and target.secret_id is not None:
                plaintext = await self._secrets.unwrap(target.secret_id)
            result = await self._executor.check_account(target, plaintext=plaintext)
            await _persist_risks(
                session,
                tenant_id=tenant_id,
                drafts=result.risks,
                source_job_type=job_type,
                source_message_id=message_id,
            )
            return result.summary

        if job_type == "account.backup":
            result = await self._executor.backup_account(target)
            managed = await _get_active_account(
                session, tenant_id=tenant_id, account_id=account_id
            )
            if managed is None or target.secret_id is None:
                raise ValueError("ACCOUNT_NOT_FOUND")
            session.add(
                AccountBackup(
                    tenant_id=tenant_id,
                    account_id=managed.id,
                    username=managed.username,
                    protocol=managed.protocol,
                    asset_id=managed.asset_id,
                    secret_id_ref=managed.secret_id,
                    metadata_json=json.dumps(
                        {
                            "rotation_policy": managed.rotation_policy,
                            "organization_id": managed.organization_id,
                            "team_id": managed.team_id,
                            "project_id": managed.project_id,
                            "status": managed.status,
                        },
                        sort_keys=True,
                    ),
                    requested_by=requested_by,
                    source_message_id=message_id,
                )
            )
            return result.summary

        raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")

    async def _load_target(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        account_id: int,
    ) -> AccountAutomationTarget:
        account = await _get_active_account(session, tenant_id=tenant_id, account_id=account_id)
        if account is None:
            raise ValueError("ACCOUNT_NOT_FOUND")
        asset = await _get_active_asset(session, tenant_id=tenant_id, asset_id=account.asset_id)
        if asset is None:
            raise ValueError("ASSET_NOT_FOUND")
        return AccountAutomationTarget(
            account_id=account.id,
            asset_id=asset.id,
            tenant_id=tenant_id,
            username=account.username,
            protocol=account.protocol,
            address=asset.address,
            port=asset.port,
            secret_id=account.secret_id,
            privileged=account.username in PRIVILEGED_USERNAMES,
        )

    async def _require_admin_target(
        self, session: AsyncSession, *, tenant_id: str, asset_id: int
    ) -> AccountAutomationTarget:
        if self._admin_resolver is not None:
            return await self._admin_resolver.resolve(
                session, tenant_id=tenant_id, asset_id=asset_id
            )
        result = await session.execute(
            select(Account, Asset)
            .join(Asset, Account.asset_id == Asset.id)
            .where(Account.tenant_id == tenant_id)
            .where(Account.asset_id == asset_id)
            .where(Account.status == "active")
            .where(Account.username.in_(tuple(PRIVILEGED_USERNAMES)))
            .order_by(Account.id)
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            raise ValueError("ADMIN_ACCOUNT_NOT_FOUND")
        account, asset = row
        return AccountAutomationTarget(
            account_id=account.id,
            asset_id=asset.id,
            tenant_id=tenant_id,
            username=account.username,
            protocol=account.protocol,
            address=asset.address,
            port=asset.port,
            secret_id=account.secret_id,
            privileged=True,
        )

    async def _with_host_key(self, target: AccountAutomationTarget) -> AccountAutomationTarget:
        if isinstance(target, SshAutomationTarget) and target.trusted_host_key:
            return target
        if self._host_keys is None:
            return target
        trusted = await self._host_keys.resolve(
            tenant_id=target.tenant_id, asset_id=target.asset_id
        )
        return SshAutomationTarget(
            account_id=target.account_id,
            asset_id=target.asset_id,
            tenant_id=target.tenant_id,
            username=target.username,
            protocol=target.protocol,
            address=target.address,
            port=target.port,
            secret_id=target.secret_id,
            privileged=target.privileged,
            shell=target.shell,
            home_dir=target.home_dir,
            groups=target.groups,
            trusted_host_key=trusted,
        )


class AdminAccountResolver(Protocol):
    async def resolve(
        self, session: AsyncSession, *, tenant_id: str, asset_id: int
    ) -> AccountAutomationTarget: ...


def bind_account_job_handler(
    handler: AccountAutomationWorkerHandler, job_type: str
) -> BoundAccountJobHandler:
    """把统一 handler 绑定到具体 ``account.*`` job_type，供 AutomationWorker 注册。"""

    return BoundAccountJobHandler(handler=handler, job_type=job_type)


class BoundAccountJobHandler:
    def __init__(self, *, handler: AccountAutomationWorkerHandler, job_type: str) -> None:
        self._handler = handler
        self._job_type = job_type

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        enriched = dict(payload)
        enriched["job_type"] = self._job_type
        await self._handler(
            tenant_id=tenant_id,
            requested_by=requested_by,
            payload=enriched,
            message_id=message_id,
        )


async def _chpasswd(
    conn: asyncssh.SSHClientConnection, *, username: str, password: str
) -> None:
    """经 stdin 向 ``chpasswd`` 喂 ``user:password``，命令行不含密码。"""

    if ":" in username or "\n" in username or "\n" in password:
        raise ValueError("ACCOUNT_PASSWORD_INPUT_INVALID")
    # 命令字符串固定为 chpasswd；密码只出现在 input= 字节流。
    result = await conn.run("chpasswd", input=f"{username}:{password}\n", check=False)
    if result.exit_status not in (0, None):
        raise ValueError(_remote_error("ACCOUNT_CHANGE_SECRET_FAILED", result))


async def _connect(
    target: SshTarget, credential: SshCredential, *, timeout: float
) -> asyncssh.SSHClientConnection:
    client_keys = None
    if credential.private_key is not None:
        try:
            key = asyncssh.import_private_key(
                credential.private_key,
                passphrase=credential.private_key_passphrase,
            )
        except asyncssh.KeyImportError as exc:
            raise SshChannelError("SSH_PRIVATE_KEY_INVALID", str(exc)) from exc
        client_keys = [key]
    trusted = target.trusted_host_key.strip()
    pattern = target.host if target.port == 22 else f"[{target.host}]:{target.port}"
    known_hosts = asyncssh.import_known_hosts(f"{pattern} {trusted}\n")
    try:
        return await asyncssh.connect(
            host=target.host,
            port=target.port,
            username=target.username,
            client_keys=client_keys,
            password=credential.password,
            passphrase=credential.private_key_passphrase,
            known_hosts=known_hosts,
            agent_path=None,
            config=None,
            kex_algs=MODERN_KEX_ALGS,
            encryption_algs=MODERN_ENCRYPTION_ALGS,
            mac_algs=MODERN_MAC_ALGS,
            server_host_key_algs=MODERN_HOST_KEY_ALGS,
            connect_timeout=timeout,
        )
    except asyncssh.HostKeyNotVerifiable as exc:
        raise SshChannelError("SSH_HOST_KEY_REJECTED", str(exc)) from exc
    except asyncssh.PermissionDenied as exc:
        raise SshChannelError("SSH_AUTH_FAILED", str(exc)) from exc
    except TimeoutError as exc:
        raise SshChannelError("SSH_CONNECT_TIMEOUT", "connection timed out") from exc
    except (asyncssh.Error, OSError) as exc:
        raise SshChannelError("SSH_CONNECT_FAILED", str(exc)) from exc


def _credential_from_plaintext(plaintext: str) -> SshCredential:
    stripped = plaintext.strip()
    if stripped.startswith("-----BEGIN") or "OPENSSH PRIVATE KEY" in stripped:
        return SshCredential(private_key=plaintext)
    return SshCredential(password=plaintext)


def _useradd_command(target: AccountAutomationTarget) -> str:
    parts = ["useradd", "--create-home"]
    if target.shell:
        parts.extend(["--shell", target.shell])
    if target.home_dir:
        parts.extend(["--home-dir", target.home_dir])
    if target.groups:
        parts.extend(["--groups", ",".join(target.groups)])
    parts.extend(["--", target.username])
    return " ".join(parts)


def _parse_passwd(stdout: str) -> list[DiscoveredAccount]:
    discovered: list[DiscoveredAccount] = []
    for line in stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 7:
            continue
        username = parts[0]
        try:
            uid = int(parts[2])
        except ValueError:
            uid = None
        discovered.append(
            DiscoveredAccount(
                username=username,
                uid=uid,
                home_dir=parts[5] or None,
                shell=parts[6] or None,
            )
        )
    return discovered


def _parse_groups(groups_json: str) -> list[str]:
    try:
        raw = json.loads(groups_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


async def _zombie_risks(
    session: AsyncSession,
    *,
    tenant_id: str,
    asset_id: int,
    discovered_usernames: set[str],
) -> tuple[AccountRiskDraft, ...]:
    """托管账号未出现在主机 passwd 中时记为僵尸账号。"""

    result = await session.execute(
        select(Account)
        .where(Account.tenant_id == tenant_id)
        .where(Account.asset_id == asset_id)
        .where(Account.status == "active")
    )
    drafts: list[AccountRiskDraft] = []
    for account in result.scalars().all():
        if account.username in discovered_usernames:
            continue
        drafts.append(
            AccountRiskDraft(
                username=account.username,
                risk_type="zombie",
                severity="medium",
                detail="managed account missing from host passwd",
                asset_id=asset_id,
                account_id=account.id,
            )
        )
    return tuple(drafts)


async def _persist_risks(
    session: AsyncSession,
    *,
    tenant_id: str,
    drafts: Sequence[AccountRiskDraft],
    source_job_type: str,
    source_message_id: str,
) -> None:
    for draft in drafts:
        existing = await session.execute(
            select(AccountRisk)
            .where(AccountRisk.tenant_id == tenant_id)
            .where(AccountRisk.asset_id == draft.asset_id)
            .where(AccountRisk.username == draft.username)
            .where(AccountRisk.risk_type == draft.risk_type)
        )
        risk = existing.scalar_one_or_none()
        if risk is None:
            session.add(
                AccountRisk(
                    tenant_id=tenant_id,
                    asset_id=draft.asset_id,
                    account_id=draft.account_id,
                    username=draft.username,
                    risk_type=draft.risk_type,
                    severity=draft.severity,
                    detail=draft.detail,
                    status="open",
                    source_job_type=source_job_type,
                    source_message_id=source_message_id,
                )
            )
        else:
            risk.severity = draft.severity
            risk.detail = draft.detail
            risk.status = "open"
            risk.account_id = draft.account_id
            risk.source_job_type = source_job_type
            risk.source_message_id = source_message_id


async def _get_active_account(
    session: AsyncSession, *, tenant_id: str, account_id: int
) -> Account | None:
    result = await session.execute(
        select(Account)
        .where(Account.id == account_id)
        .where(Account.tenant_id == tenant_id)
        .where(Account.status == "active")
    )
    return result.scalar_one_or_none()


async def _get_active_asset(
    session: AsyncSession, *, tenant_id: str, asset_id: int
) -> Asset | None:
    result = await session.execute(
        select(Asset)
        .where(Asset.id == asset_id)
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def _get_template(
    session: AsyncSession, *, tenant_id: str, template_id: int
) -> AccountTemplate | None:
    result = await session.execute(
        select(AccountTemplate)
        .where(AccountTemplate.id == template_id)
        .where(AccountTemplate.tenant_id == tenant_id)
        .where(AccountTemplate.status == "active")
    )
    return result.scalar_one_or_none()


def _generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_policy_violations(password):
            return password


def _secret_id_of(created: object) -> str:
    secret_id = getattr(created, "secret_id", None)
    if isinstance(secret_id, str) and secret_id:
        return secret_id
    if isinstance(created, str) and created:
        return created
    raise ValueError("SECRET_CREATE_FAILED")


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _remote_error(code: str, result: asyncssh.SSHCompletedProcess) -> str:
    detail = _decode(result.stderr) or _decode(result.stdout) or code
    return f"{code}:{detail[:80]}"


def _error_code(exc: Exception) -> str:
    if isinstance(exc, SshChannelError):
        return exc.code[:120]
    message = str(exc).strip()
    if message:
        return message[:120]
    return exc.__class__.__name__[:120]


def _payload_int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_optional_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_optional_str(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value
