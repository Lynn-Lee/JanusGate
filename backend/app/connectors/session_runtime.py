"""连接器侧会话运行时：会话网关 dispatch 的进程内实现（#t69 接线到 SessionGateway）。

:class:`~app.api.sessions.service.ConnectorScheduler` 这个 Protocol 是**连接器进程边界**：
生产环境 ``dispatch`` 是一次发往远端连接器进程的 RPC，凭据解析与真实通道建立都发生在
连接器侧。本模块提供该边界的**进程内实现**（dev / 单机 / 测试）：在同进程内把网关传来的
身份解析为目标 + 凭据并打开真实 SSH/SFTP 通道。

关键约束：网关只传身份（asset/account/protocol），**不持有凭据**；凭据仅在本模块（代表
连接器侧）经 :class:`SessionConnectionResolver` 解析后出现。要换成远端形态，只需另写一个
实现 ``ConnectorScheduler`` 的传输类，网关无需改动。

生产装配用资产注册表 + Vault 的 :class:`~app.connectors.asset_vault_resolver.AssetVaultSessionConnectionResolver`
替换 :class:`~app.api.sessions.service.NoopConnectorScheduler`。测试可继续注入
:class:`InMemorySessionConnectionResolver` 或 Noop。主机密钥 fail-closed，须有已批准公钥，绝不 TOFU。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.connectors.k8s_exec import K8sCredential, K8sExecChannel, K8sTarget, NamespaceScope
from app.connectors.postgres_proxy import PostgresCredential, PostgresQueryChannel, PostgresTarget
from app.connectors.ssh_channel import (
    CommandEvent,
    CommandEventSink,
    SshChannel,
    SshChannelError,
    SshCredential,
    SshProxyJump,
    SshTarget,
)
from app.connectors.ssh_interactive import SshInteractiveSession
from app.connectors.ssh_sftp import FileTransferEventSink, SftpChannel
from app.policy.schemas import ResourceRef, SubjectRef


class ConnectorSessionMode(StrEnum):
    """连接器为一个会话打开的通道形态。"""

    EXEC = "exec"
    INTERACTIVE = "interactive"
    SFTP = "sftp"
    K8S_EXEC = "k8s"
    DB_POSTGRESQL = "db_postgresql"


@dataclass(frozen=True)
class K8sConnectionBundle:
    """K8s exec 通道参数（#t68）。"""

    target: K8sTarget
    credential: K8sCredential
    scope: NamespaceScope


@dataclass(frozen=True)
class DbConnectionBundle:
    """PostgreSQL Simple Query 通道参数（#t71）。"""

    target: PostgresTarget
    credential: PostgresCredential


@dataclass(frozen=True)
class SessionConnectionSpec:
    """打开一个连接器通道所需的完整参数（含凭据，仅存在于连接器侧）。

    SSH 与 K8s 与 DB 三选一：SSH 填 ``target``/``credential``；K8s 填 ``k8s``；DB 填 ``db``。
    """

    mode: ConnectorSessionMode
    target: SshTarget | None = None
    credential: SshCredential | None = None
    proxy_jump: SshProxyJump | None = None
    k8s: K8sConnectionBundle | None = None
    db: DbConnectionBundle | None = None


# 已打开通道的联合类型：均提供 async close()。
OpenChannel = SshChannel | SshInteractiveSession | SftpChannel | K8sExecChannel | PostgresQueryChannel


@dataclass
class ConnectorSessionRecord:
    """一条已建立的连接器侧会话。

    :param connector_session_id: 连接器分配的会话 ID（回传给网关）。
    :param gateway_session_id: 对应的网关会话 ID。
    :param mode: 通道形态。
    :param channel: 已打开的底层通道对象。
    """

    connector_session_id: str
    gateway_session_id: str
    mode: ConnectorSessionMode
    channel: OpenChannel


class SessionConnectionResolver(Protocol):
    """把网关身份解析为连接参数（代表连接器侧的资产注册表 + 凭据保险库）。"""

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        """解析一次 dispatch 的连接参数。

        :raises SshChannelError: 无法解析目标/凭据（``CONNECTOR_TARGET_UNRESOLVED``）。
        """
        ...


class InMemorySessionConnectionResolver:
    """按 (tenant, asset, account, protocol) 预登记连接参数的内存 resolver（dev/测试）。"""

    def __init__(self) -> None:
        self._specs: dict[tuple[str, str, str, str], SessionConnectionSpec] = {}

    def register(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        spec: SessionConnectionSpec,
    ) -> None:
        """登记一组身份到连接参数的映射。"""

        self._specs[(tenant_id, asset_id, account_id, protocol)] = spec

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        key = (request.tenant_id, request.asset_id, request.account_id, request.protocol)
        spec = self._specs.get(key)
        if spec is None:
            raise SshChannelError(
                "CONNECTOR_TARGET_UNRESOLVED",
                f"no connection spec for asset={request.asset_id} account={request.account_id}",
            )
        return spec


class ConnectorSessionRuntime:
    """连接器侧会话运行时：解析参数、打开真实通道、登记与释放。

    :param resolver: 身份 → 连接参数解析器。
    :param command_sink: 交互式通道的命令事件下游（INTERACTIVE 模式必需）。
    :param transfer_sink: SFTP 通道的文件传输事件下游（SFTP 模式必需）。
    """

    def __init__(
        self,
        resolver: SessionConnectionResolver,
        *,
        command_sink: CommandEventSink | None = None,
        transfer_sink: FileTransferEventSink | None = None,
        command_policy: CommandPolicyGuard | None = None,
        session_factory: Callable[..., Any] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._resolver = resolver
        self._command_sink = command_sink
        self._transfer_sink = transfer_sink
        self._command_policy = command_policy
        self._session_factory = session_factory
        self._id_factory = id_factory or (lambda: f"cs_{uuid4().hex}")
        self._sessions: dict[str, ConnectorSessionRecord] = {}

    async def open(self, request: ConnectorDispatchRequest) -> ConnectorSessionRecord:
        """解析并打开一条连接器会话，登记后返回其记录。

        :raises SshChannelError: 参数不可解析、缺少所需 sink，或建立通道失败。
        """

        spec = await self._resolver.resolve(request)
        channel = await self._open_channel(spec, request)
        connector_session_id = self._id_factory()
        record = ConnectorSessionRecord(
            connector_session_id=connector_session_id,
            gateway_session_id=request.session_id,
            mode=spec.mode,
            channel=channel,
        )
        self._sessions[connector_session_id] = record
        return record

    async def close(self, connector_session_id: str) -> None:
        """释放一条连接器会话；未知 ID 视为幂等无操作。"""

        record = self._sessions.pop(connector_session_id, None)
        if record is None:
            return
        await record.channel.close()

    def get(self, connector_session_id: str) -> ConnectorSessionRecord | None:
        """按 ID 获取已登记的连接器会话记录。"""

        return self._sessions.get(connector_session_id)

    def active_ids(self) -> list[str]:
        """当前活跃连接器会话 ID 列表。"""

        return list(self._sessions)

    async def _policy_for(self, request: ConnectorDispatchRequest) -> CommandPolicyGuard:
        if self._command_policy is not None:
            return self._command_policy
        factory = self._session_factory
        if factory is None:
            from app.core.database import AsyncSessionLocal

            factory = AsyncSessionLocal
        return await default_command_policy_guard(
            subject=SubjectRef(id=request.subject_id, tenant_id=request.tenant_id),
            resource=ResourceRef(
                id=request.asset_id, type=request.protocol or "asset", tenant_id=request.tenant_id
            ),
            account_id=request.account_id,
            session_id=request.session_id,
            session_factory=factory,
        )

    async def _open_channel(
        self, spec: SessionConnectionSpec, request: ConnectorDispatchRequest
    ) -> OpenChannel:
        policy = await self._policy_for(request)
        if spec.mode is ConnectorSessionMode.K8S_EXEC:
            if spec.k8s is None:
                raise SshChannelError("K8S_SPEC_MISSING", "k8s mode requires k8s bundle")
            if self._command_sink is None:
                raise SshChannelError(
                    "CONNECTOR_COMMAND_SINK_MISSING",
                    "k8s exec mode requires a command_sink",
                )
            return await K8sExecChannel.open(
                spec.k8s.target,
                spec.k8s.credential,
                spec.k8s.scope,
                policy=policy,
            )
        if spec.mode is ConnectorSessionMode.DB_POSTGRESQL:
            if spec.db is None:
                raise SshChannelError("DB_SPEC_MISSING", "db mode requires db bundle")
            if self._command_sink is None:
                raise SshChannelError(
                    "CONNECTOR_COMMAND_SINK_MISSING",
                    "db postgresql mode requires a command_sink",
                )
            return await PostgresQueryChannel.open(
                spec.db.target,
                spec.db.credential,
                policy=policy,
            )
        if spec.target is None or spec.credential is None:
            raise SshChannelError("SSH_SPEC_MISSING", "ssh mode requires target and credential")
        proxy_kw = {"proxy_jump": spec.proxy_jump} if spec.proxy_jump is not None else {}
        if spec.mode is ConnectorSessionMode.EXEC:
            return await SshChannel.open(spec.target, spec.credential, policy=policy, **proxy_kw)
        if spec.mode is ConnectorSessionMode.INTERACTIVE:
            if self._command_sink is None:
                raise SshChannelError(
                    "CONNECTOR_COMMAND_SINK_MISSING",
                    "interactive mode requires a command_sink",
                )
            return await SshInteractiveSession.open(
                spec.target,
                spec.credential,
                self._command_sink,
                policy=policy,
                **proxy_kw,
            )
        if spec.mode is ConnectorSessionMode.SFTP:
            if self._transfer_sink is None:
                raise SshChannelError(
                    "CONNECTOR_TRANSFER_SINK_MISSING",
                    "sftp mode requires a transfer_sink",
                )
            return await SftpChannel.open(
                spec.target, spec.credential, self._transfer_sink, **proxy_kw
            )
        raise SshChannelError("CONNECTOR_UNSUPPORTED_MODE", str(spec.mode))


class ConnectorRuntimeScheduler:
    """网关 ``ConnectorScheduler`` 的进程内实现，委托给 :class:`ConnectorSessionRuntime`。

    满足 :class:`~app.api.sessions.service.ConnectorScheduler` 协议：``dispatch`` 打开真实
    连接器会话并回传 ``connector_session_id``；``release`` 关闭它。
    """

    def __init__(self, runtime: ConnectorSessionRuntime) -> None:
        self._runtime = runtime

    async def dispatch(self, request: ConnectorDispatchRequest) -> dict[str, str]:
        record = await self._runtime.open(request)
        return {
            "connector_session_id": record.connector_session_id,
            "connection_url": f"connector-runtime://{record.connector_session_id}",
        }

    async def release(self, connector_session_id: str) -> None:
        await self._runtime.close(connector_session_id)


class _NoopCommandEventSink:
    async def emit(self, event: CommandEvent) -> None:
        return None


class _NoopFileTransferEventSink:
    async def emit(self, event: object) -> None:
        return None


def build_production_connector_scheduler(
    *,
    session_factory=None,
    secrets=None,
    host_keys=None,
    scanner=None,
):
    """装配生产连接器调度器：资产注册表 + Vault + 已批准主机密钥。"""

    from hashlib import sha256

    from app.connectors.asset_vault_resolver import (
        AssetVaultSessionConnectionResolver,
        CallableSecretUnwrapper,
    )
    from app.connectors.host_key_trust import AsyncScanAdapter, HostKeyTrustStore
    from app.connectors.db_vault_resolver import (
        CallableDbSecretUnwrapper,
        DatabaseVaultSessionConnectionResolver,
    )
    from app.connectors.k8s_vault_resolver import (
        CallableK8sSecretUnwrapper,
        K8sVaultSessionConnectionResolver,
    )
    from app.connectors.routing_resolver import RoutingSessionConnectionResolver
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.policy.schemas import ApprovalState
    from app.vault.provider import (
        AsyncEnvelopeEncryptedSecretProvider,
        LocalKmsEnvelopeKeyProvider,
        SqlAlchemySecretRecordStore,
    )

    factory = session_factory or AsyncSessionLocal
    trust_store = host_keys or HostKeyTrustStore(factory)
    scan = scanner or AsyncScanAdapter()

    if secrets is None:
        master_key = settings.VAULT_LOCAL_KMS_MASTER_KEY.strip()
        if master_key:
            kms = LocalKmsEnvelopeKeyProvider.from_base64_master_key(master_key)
        else:
            kms = LocalKmsEnvelopeKeyProvider(master_key=sha256(settings.SECRET_KEY.encode()).digest())

        async def _unwrap(secret_id: str) -> str:
            async with factory() as session:
                provider = AsyncEnvelopeEncryptedSecretProvider(
                    kms_provider=kms,
                    record_store=SqlAlchemySecretRecordStore(session),
                )
                return await provider.unwrap(secret_id)

        secrets = CallableSecretUnwrapper(_unwrap)

        async def _unwrap_k8s(secret_id: str, approval: ApprovalState | None) -> str:
            async with factory() as session:
                provider = AsyncEnvelopeEncryptedSecretProvider(
                    kms_provider=kms,
                    record_store=SqlAlchemySecretRecordStore(session),
                )
                return await provider.unwrap_after_approval(secret_id, approval)

        k8s_secrets = CallableK8sSecretUnwrapper(_unwrap, _unwrap_k8s)
        db_secrets = CallableDbSecretUnwrapper(_unwrap)
    else:

        async def _unwrap_injected(secret_id: str) -> str:
            return await secrets.unwrap(secret_id)

        async def _unwrap_injected_after_approval(
            secret_id: str, approval: ApprovalState | None
        ) -> str:
            if approval is not None and not approval.is_approved_now():
                raise ValueError("SECRET_UNWRAP_APPROVAL_REQUIRED")
            return await secrets.unwrap(secret_id)

        k8s_secrets = CallableK8sSecretUnwrapper(_unwrap_injected, _unwrap_injected_after_approval)
        db_secrets = CallableDbSecretUnwrapper(_unwrap_injected)

    ssh_resolver = AssetVaultSessionConnectionResolver(
        session_factory=factory,
        secrets=secrets,
        host_keys=trust_store,
        scanner=scan,
    )
    k8s_resolver = K8sVaultSessionConnectionResolver(
        session_factory=factory,
        secrets=k8s_secrets,
    )
    db_resolver = DatabaseVaultSessionConnectionResolver(
        session_factory=factory,
        secrets=db_secrets,
    )
    resolver = RoutingSessionConnectionResolver(
        ssh_resolver=ssh_resolver,
        k8s_resolver=k8s_resolver,
        db_resolver=db_resolver,
    )
    runtime = ConnectorSessionRuntime(
        resolver,
        command_sink=_NoopCommandEventSink(),
        transfer_sink=_NoopFileTransferEventSink(),
        session_factory=factory,
    )
    return ConnectorRuntimeScheduler(runtime)

