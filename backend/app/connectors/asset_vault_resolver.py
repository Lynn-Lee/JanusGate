"""生产 SessionConnectionResolver：资产注册表 + Vault + 已批准主机密钥。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.host_key_trust import (
    SSH_CONNECT_PROTOCOLS,
    HostKeyScanner,
    HostKeyTrustStore,
    classify_presented_key,
)
from app.connectors.session_runtime import ConnectorSessionMode, SessionConnectionSpec
from app.connectors.ssh_channel import SshChannelError, SshCredential, SshTarget
from app.connectors.ssh_hostkey import HostKeyScan
from app.models.account import Account
from app.models.asset import Asset
from app.models.host_key import HostKeyPresentation

PROTOCOL_MODES: dict[str, ConnectorSessionMode] = {
    "ssh": ConnectorSessionMode.INTERACTIVE,
    "interactive": ConnectorSessionMode.INTERACTIVE,
    "exec": ConnectorSessionMode.EXEC,
    "sftp": ConnectorSessionMode.SFTP,
}


class SessionSecretUnwrapper(Protocol):
    async def unwrap(self, secret_id: str) -> str:
        """按 secret_id 解开账号凭据明文（仅内存）。"""


class MappingSecretUnwrapper:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    async def unwrap(self, secret_id: str) -> str:
        if secret_id not in self._secrets:
            raise ValueError("SECRET_NOT_FOUND")
        return self._secrets[secret_id]


class CallableSecretUnwrapper:
    def __init__(self, unwrap: Callable[[str], Awaitable[str] | str]) -> None:
        self._unwrap = unwrap

    async def unwrap(self, secret_id: str) -> str:
        value = self._unwrap(secret_id)
        if isinstance(value, str):
            return value
        return await value


class AssetVaultSessionConnectionResolver:
    """把网关身份解析为 SSH 连接参数：资产表 + 账号 Vault + 已批准主机密钥。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: SessionSecretUnwrapper,
        host_keys: HostKeyTrustStore,
        scanner: HostKeyScanner,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._host_keys = host_keys
        self._scanner = scanner

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        protocol = request.protocol.lower()
        if protocol not in SSH_CONNECT_PROTOCOLS:
            raise PermissionError("HOST_KEY_UNAPPROVED")

        async with self._session_factory() as session:
            asset = await _load_asset(session, tenant_id=request.tenant_id, asset_id=request.asset_id)
            if asset is None or not asset.is_active:
                raise SshChannelError(
                    "CONNECTOR_TARGET_UNRESOLVED",
                    f"no connection spec for asset={request.asset_id} account={request.account_id}",
                )
            account = await _load_account(
                session,
                tenant_id=request.tenant_id,
                asset_id=asset.id,
                account_id=request.account_id,
                protocol=request.protocol,
            )
            if account is None or account.status != "active":
                raise SshChannelError(
                    "CONNECTOR_TARGET_UNRESOLVED",
                    f"no connection spec for asset={request.asset_id} account={request.account_id}",
                )

        presented = await self._scan_or_deny(asset)
        trust = await self._host_keys.get(tenant_id=request.tenant_id, asset_id=str(asset.id))
        approved_key = trust.approved_public_key if trust is not None else ""
        classification = classify_presented_key(
            approved_public_key=approved_key, presented=presented
        )
        if classification.state is not HostKeyPresentation.APPROVED:
            # 未批准 / 待审批 / 已拒绝 / 密钥已变：不得自动信任（禁止 TOFU）。
            raise PermissionError("HOST_KEY_UNAPPROVED")

        try:
            plaintext = await self._secrets.unwrap(account.secret_id)
        except Exception as exc:
            raise SshChannelError(
                "CONNECTOR_TARGET_UNRESOLVED",
                f"no connection spec for asset={request.asset_id} account={request.account_id}",
            ) from exc

        return SessionConnectionSpec(
            mode=PROTOCOL_MODES.get(protocol, ConnectorSessionMode.INTERACTIVE),
            target=SshTarget(
                host=asset.address,
                port=asset.port,
                username=account.username,
                trusted_host_key=approved_key,
            ),
            credential=_credential_from_plaintext(plaintext),
        )

    async def _scan_or_deny(self, asset: Asset) -> HostKeyScan:
        try:
            return await self._scanner.scan(asset.address, asset.port)
        except Exception as exc:
            raise PermissionError("HOST_KEY_UNAPPROVED") from exc


async def _load_asset(
    session: AsyncSession, *, tenant_id: str, asset_id: str
) -> Asset | None:
    try:
        numeric_id = int(asset_id)
    except ValueError:
        return None
    result = await session.execute(
        select(Asset).where(Asset.id == numeric_id).where(Asset.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _load_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    asset_id: int,
    account_id: str,
    protocol: str,
) -> Account | None:
    stmt = select(Account).where(Account.tenant_id == tenant_id).where(Account.asset_id == asset_id)
    if account_id.isdigit():
        result = await session.execute(stmt.where(Account.id == int(account_id)))
        return result.scalar_one_or_none()
    named = stmt.where(Account.username == account_id)
    if protocol:
        typed = await session.execute(named.where(Account.protocol == protocol))
        account = typed.scalar_one_or_none()
        if account is not None:
            return account
    result = await session.execute(named)
    return result.scalars().first()


def _credential_from_plaintext(plaintext: str) -> SshCredential:
    stripped = plaintext.strip()
    if stripped.startswith("-----BEGIN") or "OPENSSH PRIVATE KEY" in stripped:
        return SshCredential(private_key=plaintext)
    return SshCredential(password=plaintext)
