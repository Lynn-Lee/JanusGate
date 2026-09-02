"""连接器侧真实 SSH 执行通道（#t69 M3 预研切片）。

本模块实现 Connector 进程内的真实 SSH 通道，验证「在强安全约束下建立单协议
端到端连接」这一 Phase 6 最高技术风险点是否可行。通道基于 ``asyncssh`` 纯 Python
实现，不 fork 任何 ``ssh`` / ``sshpass`` 子进程。

安全约束（对应 §3.6.3 历史问题，均由 ``tests/connectors/test_ssh_channel.py`` 证明关闭）：

- **P0#7  弱算法**：仅协商 :data:`MODERN_KEX_ALGS` / :data:`MODERN_ENCRYPTION_ALGS`
  / :data:`MODERN_MAC_ALGS` / :data:`MODERN_HOST_KEY_ALGS` 中的现代算法，服务端只
  提供 SHA-1 MAC、CBC 等弱算法时协商直接失败。
- **P0#15 私钥落盘**：私钥仅以内存字节/字符串经 :func:`asyncssh.import_private_key`
  加载，全程不写临时文件、不引用磁盘路径。
- **P0#16 凭据经命令行**：凭据作为库调用参数传入，无子进程、无命令行参数。
- **P0#17 AutoAddPolicy**：``known_hosts`` 由调用方提供的可信主机公钥严格构造，
  未知或不匹配的主机密钥一律拒绝连接，绝不自动信任。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import asyncssh

from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.policy.schemas import ResourceRef, SubjectRef

PROTOCOL = "ssh"

# 命令事件输出摘要上限，与 SessionCommandEventCreate.output_excerpt 的约束一致。
_OUTPUT_EXCERPT_LIMIT = 4096

# 仅允许现代密钥交换算法：排除全部 SHA-1、group1/14-sha1、gss-* 与 *.ssh.com 遗留项，
# 保留 curve25519/448、NIST ECDH、group14-sha256 及以上，以及后量子 mlkem 混合。
MODERN_KEX_ALGS: tuple[str, ...] = (
    "curve25519-sha256",
    "curve25519-sha256@libssh.org",
    "curve448-sha512",
    "mlkem768x25519-sha256",
    "ecdh-sha2-nistp256",
    "ecdh-sha2-nistp384",
    "ecdh-sha2-nistp521",
    "diffie-hellman-group16-sha512",
    "diffie-hellman-group18-sha512",
    "diffie-hellman-group14-sha256",
)

# 仅允许 AEAD 与 CTR：排除 CBC、arcfour、3des、blowfish、seed 等弱/易受攻击密码。
MODERN_ENCRYPTION_ALGS: tuple[str, ...] = (
    "chacha20-poly1305@openssh.com",
    "aes256-gcm@openssh.com",
    "aes128-gcm@openssh.com",
    "aes256-ctr",
    "aes192-ctr",
    "aes128-ctr",
)

# 仅允许 SHA-2（优先 ETM）：排除 hmac-md5、hmac-sha1 及其全部变体。
MODERN_MAC_ALGS: tuple[str, ...] = (
    "hmac-sha2-256-etm@openssh.com",
    "hmac-sha2-512-etm@openssh.com",
    "hmac-sha2-256",
    "hmac-sha2-512",
)

# 仅允许现代主机密钥算法：排除 ssh-rsa(SHA-1) 与 ssh-dss。
MODERN_HOST_KEY_ALGS: tuple[str, ...] = (
    "ssh-ed25519",
    "ssh-ed448",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "rsa-sha2-512",
    "rsa-sha2-256",
)


class SshChannelError(RuntimeError):
    """SSH 通道错误，携带稳定错误码且不承载任何凭据上下文。

    :param code: 稳定的机器可读错误码，用于审计与安全回归断言。
    :param detail: 面向运维的人类可读描述，不得包含私钥、密码等敏感信息。
    """

    def __init__(self, code: str, detail: str, *, audit_event_id: str = "") -> None:
        self.code = code
        self.detail = detail
        self.audit_event_id = audit_event_id
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class SshTarget:
    """SSH 连接目标与其可信主机公钥。

    :param host: 目标主机名或 IP。
    :param port: 目标端口。
    :param username: 登录用户名。
    :param trusted_host_key: 预置的可信主机公钥（``known_hosts``/``authorized_keys``
        单行格式，如 ``"ssh-ed25519 AAAA..."``）；连接时严格校验，不匹配即拒绝。
    """

    host: str
    port: int
    username: str
    trusted_host_key: str


@dataclass(frozen=True)
class SshProxyJump:
    """SSH ProxyJump 中继跳：经网关资产建立到内网目标的隧道（#t67）。

    网关凭据同样仅内存持有，经 Vault 解析，不经命令行传递（关闭 P0#16）。
    """

    target: SshTarget
    credential: SshCredential


@dataclass(frozen=True)
class SshCredential:
    """SSH 登录凭据，仅在内存中持有，绝不落盘。

    私钥与密码字段以 ``repr=False`` 屏蔽，并自定义 :meth:`__repr__`，避免在日志、
    异常回溯与结构化审计中意外泄露。

    :param private_key: PEM/OpenSSH 格式私钥字节或字符串（内存），或 ``None``。
    :param private_key_passphrase: 私钥口令（如有加密）。
    :param password: 口令认证使用的密码，或 ``None``。
    """

    private_key: str | bytes | None = field(default=None, repr=False)
    private_key_passphrase: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.private_key is None and self.password is None:
            raise SshChannelError(
                "SSH_CREDENTIAL_MISSING",
                "must supply an in-memory private key or a password",
            )

    def __repr__(self) -> str:  # pragma: no cover - 简单的敏感信息屏蔽
        return (
            "SshCredential("
            f"private_key={'<redacted>' if self.private_key is not None else None}, "
            f"password={'<redacted>' if self.password is not None else None})"
        )


@dataclass(frozen=True)
class CommandEvent:
    """一条命令执行事件，对齐 #t46 命令事件管线契约。

    :param sequence: 会话内单调递增的命令序号。
    :param command: 执行的命令原文。
    :param exit_code: 命令退出码，无法获取时为 ``None``。
    :param output_excerpt: 已截断的输出摘要，用于审计检索。
    """

    sequence: int
    command: str
    exit_code: int | None
    output_excerpt: str


class CommandEventSink(Protocol):
    """命令事件下游接收方（#t46 管线的注入点）。"""

    async def emit(self, event: CommandEvent) -> None:
        """接收一条命令事件；实现需保证幂等或按序号去重。"""
        ...


def _load_client_keys(credential: SshCredential) -> list[asyncssh.SSHKey] | None:
    """从内存加载客户端私钥，绝不经过磁盘路径（关闭 P0#15）。"""

    if credential.private_key is None:
        return None
    try:
        key = asyncssh.import_private_key(
            credential.private_key,
            passphrase=credential.private_key_passphrase,
        )
    except asyncssh.KeyImportError as exc:
        raise SshChannelError("SSH_PRIVATE_KEY_INVALID", str(exc)) from exc
    return [key]


def _connect_kwargs(
    target: SshTarget,
    credential: SshCredential,
    *,
    connect_timeout: float,
    tunnel: asyncssh.SSHClientConnection | None = None,
) -> dict[str, object]:
    """构造 ``asyncssh.connect`` 的安全参数集。"""

    client_keys = _load_client_keys(credential)
    known_hosts = _build_known_hosts(target)
    return {
        "host": target.host,
        "port": target.port,
        "username": target.username,
        "client_keys": client_keys,
        "password": credential.password,
        "passphrase": credential.private_key_passphrase,
        "known_hosts": known_hosts,
        "agent_path": None,
        "config": None,
        "kex_algs": MODERN_KEX_ALGS,
        "encryption_algs": MODERN_ENCRYPTION_ALGS,
        "mac_algs": MODERN_MAC_ALGS,
        "server_host_key_algs": MODERN_HOST_KEY_ALGS,
        "connect_timeout": connect_timeout,
        "tunnel": tunnel,
    }


async def _open_connection(
    target: SshTarget,
    credential: SshCredential,
    *,
    connect_timeout: float,
    proxy_jump: SshProxyJump | None = None,
) -> tuple[asyncssh.SSHClientConnection, asyncssh.SSHClientConnection | None]:
    """建立 SSH 连接，可选经 ProxyJump 中继。"""

    tunnel_connection: asyncssh.SSHClientConnection | None = None
    try:
        if proxy_jump is not None:
            tunnel_connection = await asyncssh.connect(
                **_connect_kwargs(
                    proxy_jump.target,
                    proxy_jump.credential,
                    connect_timeout=connect_timeout,
                )
            )
        connection = await asyncssh.connect(
            **_connect_kwargs(
                target,
                credential,
                connect_timeout=connect_timeout,
                tunnel=tunnel_connection,
            )
        )
    except asyncssh.HostKeyNotVerifiable as exc:
        raise SshChannelError("SSH_HOST_KEY_REJECTED", str(exc)) from exc
    except asyncssh.KeyExchangeFailed as exc:
        raise SshChannelError("SSH_ALGORITHM_NEGOTIATION_FAILED", str(exc)) from exc
    except asyncssh.PermissionDenied as exc:
        raise SshChannelError("SSH_AUTH_FAILED", str(exc)) from exc
    except TimeoutError as exc:
        raise SshChannelError("SSH_CONNECT_TIMEOUT", "connection timed out") from exc
    except (asyncssh.Error, OSError) as exc:
        raise SshChannelError("SSH_CONNECT_FAILED", str(exc)) from exc
    return connection, tunnel_connection


def _build_known_hosts(target: SshTarget) -> asyncssh.SSHKnownHosts:
    """由可信主机公钥构造 known_hosts，实现主机密钥强校验（关闭 P0#17）。"""

    trusted = target.trusted_host_key.strip()
    if not trusted:
        raise SshChannelError(
            "SSH_TRUSTED_HOST_KEY_MISSING",
            "target has no trusted host key; refusing to trust on first use",
        )
    # 非 22 端口按 OpenSSH 约定使用 ``[host]:port`` 形式，确保与实际连接端口匹配。
    pattern = target.host if target.port == 22 else f"[{target.host}]:{target.port}"
    return asyncssh.import_known_hosts(f"{pattern} {trusted}\n")


def _excerpt(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    """合并 stdout/stderr 并截断为审计摘要。

    当两者同时存在时为 stderr 预留独立预算（上限的一半），确保命令失败时承载错误
    原因的 stderr 不会被体量更大的正常输出挤出截断窗口，保住审计取证价值。
    """

    def _decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    out = _decode(stdout)
    err = _decode(stderr)
    if not err:
        return out[:_OUTPUT_EXCERPT_LIMIT]
    if not out:
        return err[:_OUTPUT_EXCERPT_LIMIT]
    err_budget = min(len(err), _OUTPUT_EXCERPT_LIMIT // 2)
    out_budget = _OUTPUT_EXCERPT_LIMIT - err_budget
    return out[:out_budget] + err[:err_budget]


class SshChannel:
    """已建立的真实 SSH 执行通道。

    通过 :meth:`open` 建立连接，:meth:`run_command` 执行命令并向命令事件管线投递
    事件，:meth:`close` 释放连接。支持异步上下文管理器语义。
    """

    def __init__(
        self,
        connection: asyncssh.SSHClientConnection,
        target: SshTarget,
        policy: CommandPolicyGuard,
        *,
        tunnel_connection: asyncssh.SSHClientConnection | None = None,
    ) -> None:
        self._connection = connection
        self._tunnel_connection = tunnel_connection
        self._target = target
        self._policy = policy

    @classmethod
    async def open(
        cls,
        target: SshTarget,
        credential: SshCredential,
        *,
        connect_timeout: float = 10.0,
        proxy_jump: SshProxyJump | None = None,
        policy: CommandPolicyGuard | None = None,
        subject: SubjectRef | None = None,
        resource: ResourceRef | None = None,
        account_id: str = "",
        session_id: str | None = None,
        session_factory: Any = None,
        db: Any = None,
    ) -> SshChannel:
        """在强安全约束下建立 SSH 连接。

        :raises SshChannelError: 主机密钥不可信（``SSH_HOST_KEY_REJECTED``）、算法
            协商失败（``SSH_ALGORITHM_NEGOTIATION_FAILED``）、认证失败
            （``SSH_AUTH_FAILED``）、超时（``SSH_CONNECT_TIMEOUT``）或其它连接错误
            （``SSH_CONNECT_FAILED``）。
        """

        connection, tunnel_connection = await _open_connection(
            target,
            credential,
            connect_timeout=connect_timeout,
            proxy_jump=proxy_jump,
        )
        resolved = policy or await default_command_policy_guard(
            subject=subject,
            resource=resource,
            account_id=account_id,
            session_id=session_id,
            session_factory=session_factory,
            db=db,
        )
        return cls(connection, target, resolved, tunnel_connection=tunnel_connection)

    async def run_command(
        self,
        command: str,
        sink: CommandEventSink,
        *,
        sequence: int,
    ) -> CommandEvent:
        """执行单条命令并向命令事件管线投递事件。

        :param command: 待执行命令。
        :param sink: 命令事件下游（#t46 管线）。
        :param sequence: 会话内命令序号。
        :returns: 已投递的命令事件。
        :raises SshChannelError: 命令被策略拒绝（``SSH_COMMAND_DENIED``）或通道异常
            （``SSH_COMMAND_FAILED``）。
        """

        decision = await self._policy.authorize(command)
        if not decision.allowed:
            raise SshChannelError(
                "SSH_COMMAND_DENIED",
                decision.reason_code,
                audit_event_id=decision.audit_event_id,
            )
        try:
            result = await self._connection.run(command, check=False)
        except asyncssh.Error as exc:
            raise SshChannelError("SSH_COMMAND_FAILED", str(exc)) from exc
        exit_code = result.exit_status if isinstance(result.exit_status, int) else None
        excerpt = self._policy.mask_text(_excerpt(result.stdout, result.stderr))
        event = CommandEvent(
            sequence=sequence,
            command=command,
            exit_code=exit_code,
            output_excerpt=excerpt,
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
        """按序执行多条命令，逐条投递命令事件。"""

        events: list[CommandEvent] = []
        for offset, command in enumerate(commands):
            events.append(
                await self.run_command(command, sink, sequence=start_sequence + offset)
            )
        return events

    async def start_interactive(
        self,
        *,
        term_type: str = "xterm",
        term_size: tuple[int, int] = (80, 24),
        encoding: str = "utf-8",
    ) -> asyncssh.SSHClientProcess[str]:
        """在已建立的安全连接上打开一个交互式 PTY 进程（登录 shell）。

        供 :mod:`app.connectors.ssh_interactive` 的交互式会话使用；所有安全约束由
        :meth:`open` 建立连接时保证，此处仅在其上申请一个带伪终端的 shell 通道。

        :param term_type: 终端类型（``TERM``）。
        :param term_size: ``(columns, rows)`` 终端尺寸。
        :param encoding: 流编码，返回 ``str`` I/O。
        :raises SshChannelError: 打开交互进程失败（``SSH_INTERACTIVE_OPEN_FAILED``）。
        """

        try:
            return await self._connection.create_process(
                term_type=term_type,
                term_size=term_size,
                encoding=encoding,
            )
        except asyncssh.Error as exc:
            raise SshChannelError("SSH_INTERACTIVE_OPEN_FAILED", str(exc)) from exc

    async def start_sftp(self) -> asyncssh.SFTPClient:
        """在已建立的安全连接上打开一个 SFTP 客户端会话。

        供 :mod:`app.connectors.ssh_sftp` 的文件传输通道使用；安全约束由
        :meth:`open` 建立连接时保证，此处仅在其上申请 SFTP 子系统。

        :raises SshChannelError: 打开 SFTP 会话失败（``SSH_SFTP_OPEN_FAILED``）。
        """

        try:
            return await self._connection.start_sftp_client()
        except asyncssh.Error as exc:
            raise SshChannelError("SSH_SFTP_OPEN_FAILED", str(exc)) from exc

    async def close(self) -> None:
        """关闭连接并等待其完全释放。"""

        self._connection.close()
        await self._connection.wait_closed()
        if self._tunnel_connection is not None:
            self._tunnel_connection.close()
            await self._tunnel_connection.wait_closed()

    async def __aenter__(self) -> SshChannel:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
