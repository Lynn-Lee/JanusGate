"""生产 SessionConnectionResolver：资产注册表 + Vault + 已批准主机密钥 + 网域 ProxyJump。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
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
from app.connectors.ssh_channel import SshChannelError, SshCredential, SshProxyJump, SshTarget
from app.connectors.ssh_hostkey import HostKeyScan
from app.models.account import Account
from app.models.asset import Asset
from app.models.host_key import HostKeyPresentation
from app.services.asset import AssetService
from app.tenancy.scope import ActorScope
from app.zones import service as zone_service

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
    """把网关身份解析为 SSH 连接参数：资产表 + 账号 Vault + 已批准主机密钥 + 网域 ProxyJump。"""

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

            scope = _connector_scope(request)
            proxy_jump = await self._resolve_proxy_jump(
                session,
                scope,
                asset=asset,
                protocol=request.protocol,
            )

        approved_key = await self._require_approved_target_key(
            request=request,
            asset=asset,
            skip_live_scan=asset.zone_id is not None,
        )

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
            proxy_jump=proxy_jump,
        )

    async def _resolve_proxy_jump(
        self,
        session: AsyncSession,
        scope: ActorScope,
        *,
        asset: Asset,
        protocol: str,
    ) -> SshProxyJump | None:
        if not asset.zone_id:
            return None

        picked = await zone_service.pick_random_active_gateway(session, scope, asset.zone_id)
        if picked is None:
            raise SshChannelError(
                "ZONE_GATEWAY_UNAVAILABLE",
                f"no active gateway for zone={asset.zone_id}",
            )
        gateway_row, gateway_asset = picked

        probe = await AssetService.test_connection(gateway_asset.address, gateway_asset.port)
        gateway_row.last_probe_at = datetime.now(UTC)
        if probe.get("reachable"):
            gateway_row.probe_status = "reachable"
            gateway_row.probe_error = ""
        else:
            gateway_row.probe_status = "unreachable"
            gateway_row.probe_error = str(probe.get("error") or "probe failed")
            await session.commit()
            raise SshChannelError(
                "ZONE_GATEWAY_UNREACHABLE",
                f"gateway asset={gateway_asset.id} unreachable",
            )
        await session.commit()

        gateway_account = await zone_service.resolve_gateway_account(
            session,
            scope,
            gateway_row=gateway_row,
            gateway_asset=gateway_asset,
            protocol=protocol,
        )
        if gateway_account is None:
            raise SshChannelError(
                "ZONE_GATEWAY_ACCOUNT_MISSING",
                f"no active account for gateway asset={gateway_asset.id}",
            )

        gateway_approved = await self._require_approved_target_key(
            request=_ConnectorKeyRequest(tenant_id=scope.tenant_id, asset_id=str(gateway_asset.id)),
            asset=gateway_asset,
            skip_live_scan=False,
        )

        try:
            gateway_plaintext = await self._secrets.unwrap(gateway_account.secret_id)
        except Exception as exc:
            raise SshChannelError(
                "ZONE_GATEWAY_ACCOUNT_MISSING",
                f"cannot unwrap gateway account for asset={gateway_asset.id}",
            ) from exc

        return SshProxyJump(
            target=SshTarget(
                host=gateway_asset.address,
                port=gateway_asset.port,
                username=gateway_account.username,
                trusted_host_key=gateway_approved,
            ),
            credential=_credential_from_plaintext(gateway_plaintext),
        )

    async def _require_approved_target_key(
        self,
        *,
        request: ConnectorDispatchRequest | _ConnectorKeyRequest,
        asset: Asset,
        skip_live_scan: bool,
    ) -> str:
        trust = await self._host_keys.get(tenant_id=request.tenant_id, asset_id=str(asset.id))
        approved_key = trust.approved_public_key if trust is not None else ""
        if skip_live_scan:
            if not approved_key:
                raise PermissionError("HOST_KEY_UNAPPROVED")
            return approved_key

        presented = await self._scan_or_deny(asset)
        classification = classify_presented_key(
            approved_public_key=approved_key, presented=presented
        )
        if classification.state is not HostKeyPresentation.APPROVED:
            raise PermissionError("HOST_KEY_UNAPPROVED")
        return approved_key

    async def _scan_or_deny(self, asset: Asset) -> HostKeyScan:
        try:
            return await self._scanner.scan(asset.address, asset.port)
        except Exception as exc:
            raise PermissionError("HOST_KEY_UNAPPROVED") from exc


class _ConnectorKeyRequest:
    def __init__(self, *, tenant_id: str, asset_id: str) -> None:
        self.tenant_id = tenant_id
        self.asset_id = asset_id


def _connector_scope(request: ConnectorDispatchRequest) -> ActorScope:
    """连接器解析网域网关时使用租户级可见范围。"""

    return ActorScope(
        user_id=request.subject_id,
        tenant_id=request.tenant_id,
        permissions=("admin",),
    )


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
