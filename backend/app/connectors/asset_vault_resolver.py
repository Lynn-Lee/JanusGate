"""生产 SessionConnectionResolver：资产注册表 + Vault + 已批准主机密钥 / K8s CA。"""

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
from app.connectors.k8s_exec import (
    K8sChannelError,
    K8sCredential,
    K8sPodInfo,
    K8sTarget,
    NamespaceScope,
    assert_k8s_https_ca,
    assert_single_namespace,
    list_namespaced_pods,
)
from app.connectors.session_runtime import ConnectorSessionMode, SessionConnectionSpec
from app.connectors.ssh_channel import SshChannelError, SshCredential, SshTarget
from app.connectors.ssh_hostkey import HostKeyScan
from app.models.account import Account
from app.models.asset import Asset
from app.models.host_key import HostKeyPresentation

K8S_CONNECT_PROTOCOLS = frozenset({"k8s", "kubernetes"})
K8S_HTTPS_CA_DENIED_COPY = "无法连接（需要 HTTPS 和 CA）"
K8S_CONNECT_DENIED_COPY = "无法连接"

PROTOCOL_MODES: dict[str, ConnectorSessionMode] = {
    "ssh": ConnectorSessionMode.INTERACTIVE,
    "interactive": ConnectorSessionMode.INTERACTIVE,
    "exec": ConnectorSessionMode.EXEC,
    "sftp": ConnectorSessionMode.SFTP,
    "k8s": ConnectorSessionMode.K8S,
    "kubernetes": ConnectorSessionMode.K8S,
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
    """把网关身份解析为 SSH/K8s 连接参数：资产表 + 账号 Vault。

    SSH 走已批准主机密钥（fail-closed，禁止 TOFU）。K8s 走 API URL + 预置 CA +
    单一 namespace（建连前强制，仅资产上的 ns），Bearer token 仅内存持有。
    Pod 由建连弹层传入，不落库。
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: SessionSecretUnwrapper,
        host_keys: HostKeyTrustStore,
        scanner: HostKeyScanner,
        k8s_pod_lister=list_namespaced_pods,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._host_keys = host_keys
        self._scanner = scanner
        self._k8s_pod_lister = k8s_pod_lister

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        protocol = request.protocol.lower()
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

        if protocol in K8S_CONNECT_PROTOCOLS:
            return await self._resolve_k8s(request, asset, account)
        if protocol not in SSH_CONNECT_PROTOCOLS:
            raise SshChannelError("CONNECTOR_PROTOCOL_UNSUPPORTED", protocol)
        return await self._resolve_ssh(request, asset, account, protocol)

    async def list_k8s_pods(self, request: ConnectorDispatchRequest) -> tuple[str, list[K8sPodInfo]]:
        """列出资产单一 namespace 内可 exec 的 Pod；越权/缺 HTTPS+CA/缺 ns 在打开弹层前失败。"""

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
                protocol=request.protocol or "k8s",
            )
            if account is None or account.status != "active":
                raise SshChannelError(
                    "CONNECTOR_TARGET_UNRESOLVED",
                    f"no connection spec for asset={request.asset_id} account={request.account_id}",
                )

        api_server, server_ca, namespace, scope = self._prepare_k8s_target(asset)
        token = await self._unwrap_secret(request, account)
        pods = await self._k8s_pod_lister(
            api_server=api_server,
            namespace=namespace,
            server_ca=server_ca,
            credential=K8sCredential(token=token),
            scope=scope,
        )
        return namespace, pods

    async def _resolve_ssh(
        self,
        request: ConnectorDispatchRequest,
        asset: Asset,
        account: Account,
        protocol: str,
    ) -> SessionConnectionSpec:
        presented = await self._scan_or_deny(asset)
        trust = await self._host_keys.get(tenant_id=request.tenant_id, asset_id=str(asset.id))
        approved_key = trust.approved_public_key if trust is not None else ""
        classification = classify_presented_key(
            approved_public_key=approved_key, presented=presented
        )
        if classification.state is not HostKeyPresentation.APPROVED:
            raise PermissionError("HOST_KEY_UNAPPROVED")

        plaintext = await self._unwrap_secret(request, account)
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

    def _prepare_k8s_target(self, asset: Asset) -> tuple[str, str, str, NamespaceScope]:
        api_server = _k8s_api_server(asset)
        server_ca = (getattr(asset, "server_ca", None) or "").strip()
        try:
            api_server = assert_k8s_https_ca(api_server, server_ca)
        except K8sChannelError as exc:
            if exc.code in {"K8S_INSECURE_TRANSPORT", "K8S_TLS_CA_MISSING"}:
                raise K8sChannelError("K8S_HTTPS_CA_REQUIRED", K8S_HTTPS_CA_DENIED_COPY) from exc
            raise
        # Namespace lives only on the asset. Do not read account.namespace
        # even if that column exists from a previous one-click pass.
        namespace = (getattr(asset, "namespace", None) or "").strip()
        if not namespace:
            raise K8sChannelError("K8S_NAMESPACE_MISSING", K8S_CONNECT_DENIED_COPY)
        scope = NamespaceScope(namespaces=frozenset({namespace}))
        assert_single_namespace(scope, namespace)
        return api_server, server_ca, namespace, scope

    async def _resolve_k8s(
        self,
        request: ConnectorDispatchRequest,
        asset: Asset,
        account: Account,
    ) -> SessionConnectionSpec:
        api_server, server_ca, namespace, scope = self._prepare_k8s_target(asset)
        pod = (request.pod or "").strip()
        if not pod:
            raise K8sChannelError("K8S_POD_REQUIRED", K8S_CONNECT_DENIED_COPY)
        container = (request.container or "").strip() or None
        token = await self._unwrap_secret(request, account)
        return SessionConnectionSpec(
            mode=ConnectorSessionMode.K8S,
            k8s_target=K8sTarget(
                api_server=api_server,
                namespace=namespace,
                pod=pod,
                container=container,
                server_ca=server_ca,
            ),
            k8s_credential=K8sCredential(token=token),
            k8s_scope=scope,
        )

    async def _unwrap_secret(self, request: ConnectorDispatchRequest, account: Account) -> str:
        try:
            return await self._secrets.unwrap(account.secret_id)
        except K8sChannelError:
            raise
        except Exception as exc:
            raise SshChannelError(
                "CONNECTOR_TARGET_UNRESOLVED",
                f"no connection spec for asset={request.asset_id} account={request.account_id}",
            ) from exc

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


def _k8s_api_server(asset: Asset) -> str:
    address = (asset.address or "").strip()
    if not address:
        return ""
    lowered = address.lower()
    if lowered.startswith("http://"):
        return address
    if lowered.startswith("https://"):
        return address
    port = asset.port or 443
    return f"https://{address}:{port}"
