"""连接器侧真实 K8s ``exec`` 通道（#t72 M3：kubectl exec 语义通道）。

本模块实现 Connector 进程内到 Kubernetes API Server 的 ``pods/exec`` 通道，走
WebSocket 上的 ``v4.channel.k8s.io`` 子协议（stdin/stdout/stderr/error 单字节
通道多路复用），把「在 Pod 中执行一条命令」映射为一条对齐 #t46 命令事件管线的
:class:`~app.connectors.ssh_channel.CommandEvent`，与 SSH 通道复用同一审计管线。
基于纯 Python 的 ``websockets``，不 fork ``kubectl`` 子进程。

安全约束（对应 roadmap #t72「namespace 作用域强制生效」与 §3.6.3 历史问题，
均由 ``tests/connectors/test_k8s_exec.py`` 证明关闭）：

- **namespace 作用域强制**：通道以 :class:`NamespaceScope` 授权，目标 namespace 不在
  授权集合内一律拒绝（``K8S_NAMESPACE_FORBIDDEN``），在建连之前即阻断——即使调用方
  透传了越权的 :class:`K8sTarget`，也不会向该 namespace 发起 exec。
- **TLS 强校验**（对标 SSH 的 P0#17 主机密钥强校验）：API Server 证书由调用方预置的
  CA（``server_ca``）严格校验且校验主机名，未提供 CA 一律拒绝（``K8S_TLS_CA_MISSING``），
  绝不 trust on first use、绝不暴露关闭校验的开关。
- **凭据仅内存、不经命令行**（对标 P0#15/P0#16）：Bearer token 仅在内存持有、经
  ``Authorization`` 请求头发送，绝不进入 URL query 或任何命令行；:class:`K8sCredential`
  的 token 字段在 ``repr`` 中屏蔽，避免日志与审计意外泄露。
"""

from __future__ import annotations

import contextlib
import json
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from websockets import Subprotocol
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedOK, InvalidHandshake

from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.connectors.ssh_channel import CommandEvent, CommandEventSink

PROTOCOL = "k8s"

# 命令事件输出摘要上限，与 SessionCommandEventCreate.output_excerpt 的约束一致。
_OUTPUT_EXCERPT_LIMIT = 4096

# ``v4.channel.k8s.io`` 子协议的通道编号：每条二进制帧首字节即通道号。
_CHANNEL_STDIN = 0
_CHANNEL_STDOUT = 1
_CHANNEL_STDERR = 2
_CHANNEL_ERROR = 3

# K8s streaming exec 使用的 WebSocket 子协议标识（v4：error 通道回传 metav1.Status）。
V4_CHANNEL_SUBPROTOCOL = "v4.channel.k8s.io"

# 默认以 ``/bin/sh -c`` 包裹操作员命令，对齐 kubectl exec 的常见交互式用法；审计事件
# 记录的是操作员命令原文，而非包裹后的 argv。
_DEFAULT_EXEC_SHELL = "/bin/sh"


class K8sChannelError(RuntimeError):
    """K8s exec 通道错误，携带稳定错误码且不承载任何凭据上下文。

    :param code: 稳定的机器可读错误码，用于审计与安全回归断言。
    :param detail: 面向运维的人类可读描述，不得包含 token 等敏感信息。
    """

    def __init__(self, code: str, detail: str, *, audit_event_id: str = "") -> None:
        self.code = code
        self.detail = detail
        self.audit_event_id = audit_event_id
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class NamespaceScope:
    """一次会话被授权访问的 K8s namespace 作用域。

    作用域是连接器侧的**强制边界**：通道只会对集合内的 namespace 建连 exec，越权目标在
    建连前即被拒绝。集合来源应是授权模型（资产授权 / #t68 namespace 作用域），不得由
    不可信输入直接构造。

    :param namespaces: 被授权的 namespace 名称集合；空集合表示不授权任何 namespace。
    """

    namespaces: frozenset[str]

    def allows(self, namespace: str) -> bool:
        """判定 ``namespace`` 是否在授权集合内。"""

        return namespace in self.namespaces


@dataclass(frozen=True)
class K8sTarget:
    """K8s exec 连接目标与其 API Server 可信 CA。

    :param api_server: API Server 基址，必须为 ``https://host:port`` 形式；内部转换为
        ``wss://`` 建立 WebSocket，拒绝明文 ``http``。
    :param namespace: 目标 Pod 所在 namespace；须落在 :class:`NamespaceScope` 授权内。
    :param pod: 目标 Pod 名称。
    :param container: 目标容器名；``None`` 时由 API Server 选择默认容器。
    :param server_ca: 严格校验 API Server 证书用的 CA 证书（PEM）；为空即拒绝建连，
        不做 trust on first use。
    """

    api_server: str
    namespace: str
    pod: str
    container: str | None = None
    server_ca: str | None = None


@dataclass(frozen=True)
class K8sCredential:
    """K8s 访问凭据，仅在内存持有，经 ``Authorization`` 头发送，绝不进入命令行/URL。

    token 字段以 ``repr=False`` 屏蔽并自定义 :meth:`__repr__`，避免在日志、异常回溯与
    结构化审计中意外泄露。

    :param token: ServiceAccount / OIDC Bearer token（内存字符串）。
    """

    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.token:
            raise K8sChannelError(
                "K8S_CREDENTIAL_MISSING",
                "must supply an in-memory bearer token",
            )

    def __repr__(self) -> str:  # pragma: no cover - 简单的敏感信息屏蔽
        return "K8sCredential(token=<redacted>)"


def _excerpt(stdout: bytes, stderr: bytes) -> str:
    """合并 stdout/stderr 并截断为审计摘要。

    与 SSH 通道一致：两者同时存在时为 stderr 预留独立预算（上限的一半），确保命令失败时
    承载错误原因的 stderr 不会被体量更大的正常输出挤出截断窗口，保住审计取证价值。
    """

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if not err:
        return out[:_OUTPUT_EXCERPT_LIMIT]
    if not out:
        return err[:_OUTPUT_EXCERPT_LIMIT]
    err_budget = min(len(err), _OUTPUT_EXCERPT_LIMIT // 2)
    out_budget = _OUTPUT_EXCERPT_LIMIT - err_budget
    return out[:out_budget] + err[:err_budget]


def _parse_exit_status(payload: bytes | None) -> int | None:
    """从 error 通道（channel 3）的 ``metav1.Status`` JSON 解析退出码。

    K8s 在进程结束时于 error 通道回传一个状态对象：``status=="Success"`` 记为退出码 0；
    非零退出时 ``status=="Failure"`` 且 ``details.causes`` 内含 ``reason=="ExitCode"`` 的
    退出码。无法解析（无状态帧 / 非 JSON / 失败但未带退出码）时返回 ``None``，与
    :class:`CommandEvent` 允许 ``exit_code`` 为 ``None`` 的契约一致。
    """

    if not payload:
        return None
    try:
        status = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(status, dict):
        return None
    if status.get("status") == "Success":
        return 0
    details = status.get("details")
    if isinstance(details, dict):
        for cause in details.get("causes") or []:
            if isinstance(cause, dict) and cause.get("reason") == "ExitCode":
                message = cause.get("message")
                if isinstance(message, str | int):
                    try:
                        return int(message)
                    except ValueError:
                        return None
    return None


def _build_ssl_context(target: K8sTarget) -> ssl.SSLContext:
    """由 ``server_ca`` 构造强校验的客户端 SSL 上下文（关闭 trust on first use）。

    :raises K8sChannelError: 未提供 CA（``K8S_TLS_CA_MISSING``）或 CA 无法解析
        （``K8S_TLS_CA_INVALID``）。
    """

    ca = (target.server_ca or "").strip()
    if not ca:
        raise K8sChannelError(
            "K8S_TLS_CA_MISSING",
            "target has no trusted API server CA; refusing to trust on first use",
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_verify_locations(cadata=ca)
    except ssl.SSLError as exc:
        raise K8sChannelError("K8S_TLS_CA_INVALID", str(exc)) from exc
    return context


class K8sExecChannel:
    """已授权的 K8s ``exec`` 通道。

    通过 :meth:`open` 校验 namespace 作用域并构造强校验 TLS 上下文，:meth:`run_command`
    对 Pod 执行单条命令、解析退出码并向命令事件管线投递事件。K8s exec 语义下每条命令
    对应一次独立的 WebSocket 连接，故 :meth:`close` 无长连接可释放（保留以对齐通道语义
    与上下文管理器用法）。
    """

    def __init__(
        self,
        target: K8sTarget,
        credential: K8sCredential,
        ssl_context: ssl.SSLContext,
        *,
        connect_timeout: float,
        exec_shell: str,
        policy: CommandPolicyGuard,
    ) -> None:
        self._target = target
        self._credential = credential
        self._ssl = ssl_context
        self._connect_timeout = connect_timeout
        self._exec_shell = exec_shell
        self._policy = policy

    @classmethod
    async def open(
        cls,
        target: K8sTarget,
        credential: K8sCredential,
        scope: NamespaceScope,
        *,
        connect_timeout: float = 10.0,
        exec_shell: str = _DEFAULT_EXEC_SHELL,
        policy: CommandPolicyGuard | None = None,
        subject=None,
        resource=None,
        account_id: str = "",
        session_id: str | None = None,
        session_factory=None,
        db=None,
    ) -> K8sExecChannel:
        """在 namespace 作用域与 TLS 强校验约束下准备 exec 通道。

        :raises K8sChannelError: 目标 namespace 越权（``K8S_NAMESPACE_FORBIDDEN``）、
            API Server 非 https（``K8S_INSECURE_TRANSPORT``）、缺失/非法 CA
            （``K8S_TLS_CA_MISSING`` / ``K8S_TLS_CA_INVALID``）。
        """

        if not scope.allows(target.namespace):
            raise K8sChannelError(
                "K8S_NAMESPACE_FORBIDDEN",
                f"namespace {target.namespace!r} is not within the granted scope",
            )
        if not target.api_server.startswith("https://"):
            raise K8sChannelError(
                "K8S_INSECURE_TRANSPORT",
                "api_server must be an https:// endpoint",
            )
        ssl_context = _build_ssl_context(target)
        return cls(
            target,
            credential,
            ssl_context,
            connect_timeout=connect_timeout,
            exec_shell=exec_shell,
            policy=policy
            or await default_command_policy_guard(
                subject=subject,
                resource=resource,
                account_id=account_id,
                session_id=session_id,
                session_factory=session_factory,
                db=db,
            ),
        )

    def _exec_url(self, command: str) -> str:
        """构造 ``pods/{pod}/exec`` 的 wss URL；命令经 ``/bin/sh -c`` 包裹为 argv。"""

        base = self._target.api_server.rstrip("/")
        wss_base = "wss://" + base[len("https://") :]
        namespace = quote(self._target.namespace, safe="")
        pod = quote(self._target.pod, safe="")
        # command 以重复 query 参数逐个传递 argv；token 不在此出现（走 Authorization 头）。
        params: list[tuple[str, str]] = [
            ("stdin", "false"),
            ("stdout", "true"),
            ("stderr", "true"),
            ("tty", "false"),
        ]
        if self._target.container is not None:
            params.append(("container", self._target.container))
        for arg in (self._exec_shell, "-c", command):
            params.append(("command", arg))
        return f"{wss_base}/api/v1/namespaces/{namespace}/pods/{pod}/exec?{urlencode(params)}"

    async def run_command(
        self,
        command: str,
        sink: CommandEventSink,
        *,
        sequence: int,
    ) -> CommandEvent:
        """在目标 Pod 中执行单条命令并向命令事件管线投递事件。

        :param command: 待执行命令（经 ``exec_shell -c`` 包裹）。
        :param sink: 命令事件下游（#t46 管线）。
        :param sequence: 会话内命令序号。
        :returns: 已投递的命令事件。
        :raises K8sChannelError: 命令被策略拒绝（``K8S_COMMAND_DENIED``）、TLS 握手失败
            （``K8S_TLS_HANDSHAKE_FAILED``）、API Server 拒绝握手/鉴权（``K8S_EXEC_REJECTED``）、
            超时（``K8S_CONNECT_TIMEOUT``）或其它连接错误（``K8S_CONNECT_FAILED``）。
        """

        decision = await self._policy.authorize(command)
        if not decision.allowed:
            raise K8sChannelError(
                "K8S_COMMAND_DENIED",
                decision.reason_code,
                audit_event_id=decision.audit_event_id,
            )
        url = self._exec_url(command)
        headers = {"Authorization": f"Bearer {self._credential.token}"}
        try:
            async with ws_connect(
                url,
                additional_headers=headers,
                subprotocols=[Subprotocol(V4_CHANNEL_SUBPROTOCOL)],
                ssl=self._ssl,
                open_timeout=self._connect_timeout,
            ) as connection:
                stdout, stderr, status = await self._pump(connection)
        except ssl.SSLError as exc:
            raise K8sChannelError("K8S_TLS_HANDSHAKE_FAILED", str(exc)) from exc
        except InvalidHandshake as exc:
            # 覆盖 InvalidStatus（API Server 返回 401/403 鉴权/RBAC 拒绝）等握手失败。
            raise K8sChannelError("K8S_EXEC_REJECTED", str(exc)) from exc
        except TimeoutError as exc:
            raise K8sChannelError("K8S_CONNECT_TIMEOUT", "connection timed out") from exc
        except OSError as exc:
            raise K8sChannelError("K8S_CONNECT_FAILED", str(exc)) from exc

        event = CommandEvent(
            sequence=sequence,
            command=command,
            exit_code=_parse_exit_status(status),
            output_excerpt=self._policy.mask_text(_excerpt(stdout, stderr)),
        )
        await sink.emit(event)
        return event

    async def run_script(
        self,
        commands: Sequence[str],
        sink: CommandEventSink,
        *,
        start_sequence: int = 0,
    ) -> list[CommandEvent]:
        """按序执行多条命令，逐条投递命令事件（每条命令一次独立 exec 连接）。"""

        events: list[CommandEvent] = []
        for offset, command in enumerate(commands):
            events.append(
                await self.run_command(command, sink, sequence=start_sequence + offset)
            )
        return events

    @staticmethod
    async def _pump(connection: ClientConnection) -> tuple[bytes, bytes, bytes | None]:
        """读取 exec WebSocket 的多路复用帧，按通道号归集 stdout/stderr/status。

        每帧首字节为通道号，其余为该通道数据。持续读取直到对端关闭连接；error 通道
        （channel 3）帧作为 ``metav1.Status`` 保留最后一条用于退出码解析。
        """

        stdout = bytearray()
        stderr = bytearray()
        status: bytes | None = None
        with contextlib.suppress(ConnectionClosedOK):
            while True:
                message = await connection.recv()
                frame = message if isinstance(message, bytes) else message.encode("utf-8")
                if not frame:
                    continue
                channel, data = frame[0], frame[1:]
                if channel == _CHANNEL_STDOUT:
                    stdout.extend(data)
                elif channel == _CHANNEL_STDERR:
                    stderr.extend(data)
                elif channel == _CHANNEL_ERROR:
                    status = bytes(data)
        return bytes(stdout), bytes(stderr), status

    async def close(self) -> None:
        """释放通道；K8s exec 每条命令一次连接，无长连接可释放（对齐通道语义）。"""

    async def __aenter__(self) -> K8sExecChannel:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
