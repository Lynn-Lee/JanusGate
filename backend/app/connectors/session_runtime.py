"""连接器侧会话运行时：会话网关 dispatch 的进程内实现（#t69 接线到 SessionGateway）。

:class:`~app.api.sessions.service.ConnectorScheduler` 这个 Protocol 是**连接器进程边界**：
生产环境 ``dispatch`` 是一次发往远端连接器进程的 RPC，凭据解析与真实通道建立都发生在
连接器侧。本模块提供该边界的**进程内实现**（dev / 单机 / 测试）：在同进程内把网关传来的
身份解析为目标 + 凭据并打开真实 SSH/SFTP 通道。

关键约束：网关只传身份（asset/account/protocol），**不持有凭据**；凭据仅在本模块（代表
连接器侧）经 :class:`SessionConnectionResolver` 解析后出现。要换成远端形态，只需另写一个
实现 ``ConnectorScheduler`` 的传输类，网关无需改动。

注意：路由默认仍使用 :class:`~app.api.sessions.service.NoopConnectorScheduler`；本运行时
在生产接线需要一个把资产注册表 + 凭据保险库桥接进来的 :class:`SessionConnectionResolver`
实现（尚未落地），故此处先交付机制与测试，待 resolver 就绪再在装配层启用。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.ssh_channel import (
    CommandEventSink,
    SshChannel,
    SshChannelError,
    SshCredential,
    SshTarget,
)
from app.connectors.ssh_interactive import SshInteractiveSession
from app.connectors.ssh_sftp import FileTransferEventSink, SftpChannel


class ConnectorSessionMode(StrEnum):
    """连接器为一个会话打开的通道形态。"""

    EXEC = "exec"
    INTERACTIVE = "interactive"
    SFTP = "sftp"


@dataclass(frozen=True)
class SessionConnectionSpec:
    """打开一个连接器通道所需的完整参数（含凭据，仅存在于连接器侧）。

    :param mode: 通道形态。
    :param target: SSH 目标与可信主机公钥。
    :param credential: 内存凭据（私钥或密码）。
    """

    mode: ConnectorSessionMode
    target: SshTarget
    credential: SshCredential


# 已打开通道的联合类型：三者均提供 async close()，故运行时可统一关闭。
OpenChannel = SshChannel | SshInteractiveSession | SftpChannel


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
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._resolver = resolver
        self._command_sink = command_sink
        self._transfer_sink = transfer_sink
        self._id_factory = id_factory or (lambda: f"cs_{uuid4().hex}")
        self._sessions: dict[str, ConnectorSessionRecord] = {}

    async def open(self, request: ConnectorDispatchRequest) -> ConnectorSessionRecord:
        """解析并打开一条连接器会话，登记后返回其记录。

        :raises SshChannelError: 参数不可解析、缺少所需 sink，或建立通道失败。
        """

        spec = await self._resolver.resolve(request)
        channel = await self._open_channel(spec)
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

    async def _open_channel(self, spec: SessionConnectionSpec) -> OpenChannel:
        if spec.mode is ConnectorSessionMode.EXEC:
            return await SshChannel.open(spec.target, spec.credential)
        if spec.mode is ConnectorSessionMode.INTERACTIVE:
            if self._command_sink is None:
                raise SshChannelError(
                    "CONNECTOR_COMMAND_SINK_MISSING",
                    "interactive mode requires a command_sink",
                )
            return await SshInteractiveSession.open(
                spec.target, spec.credential, self._command_sink
            )
        if spec.mode is ConnectorSessionMode.SFTP:
            if self._transfer_sink is None:
                raise SshChannelError(
                    "CONNECTOR_TRANSFER_SINK_MISSING",
                    "sftp mode requires a transfer_sink",
                )
            return await SftpChannel.open(spec.target, spec.credential, self._transfer_sink)
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
