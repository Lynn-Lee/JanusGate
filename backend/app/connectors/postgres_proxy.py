"""连接器侧 PostgreSQL Simple Query 代理通道（#t71 M3：数据库协议代理）。

实现 PostgreSQL 3.0 线协议的 **Simple Query**（``Q`` 消息）子集：在连接器进程内建立
TCP/TLS 连接、完成 startup/auth，把每条 SQL 映射为对齐 #t46 命令事件管线的
:class:`~app.connectors.ssh_channel.CommandEvent`，与 SSH/K8s 通道复用同一审计与脱敏管线。
纯 Python ``asyncio`` 实现，不 fork ``psql`` 子进程、不依赖 ``psycopg2``。

安全约束（对应 roadmap #t71 与 §3.6.3 历史问题，由 ``tests/connectors/test_postgres_proxy.py`` 证明）：

- **凭据仅内存、不经命令行**（对标 P0#15/P0#16）：密码仅在 startup/auth 报文中传递，
  :class:`PostgresCredential` 的 ``repr`` 屏蔽，日志与异常不得携带明文。
- **TLS 强校验**（可选）：当 ``require_tls=True`` 时须预置 ``server_ca`` PEM，拒绝 TOFU。
- **命令策略执行前强制**：``CommandPolicyGuard.authorize(sql)`` 在发往数据库之前阻断。
- **结果脱敏**：``CommandPolicyGuard.mask_text`` 在写入 ``CommandEvent.output_excerpt`` 前应用。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import ssl
import struct
from dataclasses import dataclass, field
from typing import Any

from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.connectors.ssh_channel import CommandEvent, CommandEventSink
from app.policy.schemas import ResourceRef, SubjectRef

PROTOCOL = "postgresql"
_PROTOCOL_VERSION = 196608  # 3.0
_OUTPUT_EXCERPT_LIMIT = 4096

_MSG_AUTH_REQUEST = b"R"
_MSG_PASSWORD = b"p"
_MSG_QUERY = b"Q"
_MSG_COMMAND_COMPLETE = b"C"
_MSG_DATA_ROW = b"D"
_MSG_ERROR = b"E"
_MSG_READY = b"Z"


class PostgresChannelError(RuntimeError):
    """PostgreSQL 代理通道错误，携带稳定错误码且不承载凭据上下文。"""

    def __init__(self, code: str, detail: str, *, audit_event_id: str = "") -> None:
        self.code = code
        self.detail = detail
        self.audit_event_id = audit_event_id
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class PostgresTarget:
    """PostgreSQL 连接目标。

    :param host: 数据库主机地址。
    :param port: 数据库端口。
    :param database: 连接的数据库名。
    :param username: 数据库角色名。
    :param require_tls: 为 ``True`` 时强制 TLS 且须提供 ``server_ca``。
    :param server_ca: TLS 校验用 CA PEM；``require_tls=True`` 时必填。
    """

    host: str
    port: int
    database: str
    username: str
    require_tls: bool = False
    server_ca: str | None = None


@dataclass(frozen=True)
class PostgresCredential:
    """PostgreSQL 密码凭据，仅内存持有。"""

    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.password:
            raise PostgresChannelError(
                "PG_CREDENTIAL_MISSING",
                "must supply an in-memory database password",
            )

    def __repr__(self) -> str:  # pragma: no cover
        return "PostgresCredential(password=<redacted>)"


def _pack_message(msg_type: bytes, payload: bytes) -> bytes:
    return msg_type + struct.pack("!I", len(payload) + 4) + payload


def _build_startup_message(username: str, database: str) -> bytes:
    body = struct.pack("!I", _PROTOCOL_VERSION)
    for key, value in (("user", username), ("database", database), ("client_encoding", "UTF8")):
        body += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
    body += b"\x00"
    return struct.pack("!I", len(body) + 4) + body


async def _read_message(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    msg_type = await reader.readexactly(1)
    (length,) = struct.unpack("!I", await reader.readexactly(4))
    payload = await reader.readexactly(length - 4)
    return msg_type, payload


def _parse_error(payload: bytes) -> str:
    parts: list[str] = []
    idx = 0
    while idx < len(payload):
        end = payload.find(b"\x00", idx)
        if end == -1:
            break
        key = payload[idx:end].decode("ascii", errors="replace")
        idx = end + 1
        end = payload.find(b"\x00", idx)
        if end == -1:
            break
        value = payload[idx:end].decode("utf-8", errors="replace")
        idx = end + 1
        if key in {"M", "D", "H"}:
            parts.append(value)
    return "; ".join(parts) if parts else "query failed"


def _parse_data_row(payload: bytes) -> str:
    if len(payload) < 2:
        return ""
    column_count = struct.unpack("!h", payload[:2])[0]
    idx = 2
    values: list[str] = []
    for _ in range(column_count):
        (col_len,) = struct.unpack("!i", payload[idx : idx + 4])
        idx += 4
        if col_len == -1:
            values.append("NULL")
            continue
        values.append(payload[idx : idx + col_len].decode("utf-8", errors="replace"))
        idx += col_len
    return "\t".join(values)


def _parse_command_complete(payload: bytes) -> tuple[int | None, str]:
    tag = payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    match = re.search(r"(\d+)\s*$", tag)
    if match:
        return 0, tag
    if tag.upper().startswith("INSERT") or tag.upper().startswith("UPDATE") or tag.upper().startswith("DELETE"):
        return 0, tag
    return 0, tag


def _build_ssl_context(target: PostgresTarget) -> ssl.SSLContext | None:
    if not target.require_tls:
        return None
    ca = (target.server_ca or "").strip()
    if not ca:
        raise PostgresChannelError(
            "PG_TLS_CA_MISSING",
            "target requires TLS but has no trusted server CA; refusing to trust on first use",
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_verify_locations(cadata=ca)
    except ssl.SSLError as exc:
        raise PostgresChannelError("PG_TLS_CA_INVALID", str(exc)) from exc
    return context


def _excerpt(stdout: str, stderr: str) -> str:
    if not stderr:
        return stdout[:_OUTPUT_EXCERPT_LIMIT]
    if not stdout:
        return stderr[:_OUTPUT_EXCERPT_LIMIT]
    err_budget = min(len(stderr), _OUTPUT_EXCERPT_LIMIT // 2)
    out_budget = _OUTPUT_EXCERPT_LIMIT - err_budget
    return stdout[:out_budget] + stderr[:err_budget]


class PostgresQueryChannel:
    """已授权的 PostgreSQL Simple Query 通道。"""

    def __init__(
        self,
        target: PostgresTarget,
        credential: PostgresCredential,
        *,
        ssl_context: ssl.SSLContext | None,
        connect_timeout: float,
        policy: CommandPolicyGuard,
    ) -> None:
        self._target = target
        self._credential = credential
        self._ssl = ssl_context
        self._connect_timeout = connect_timeout
        self._policy = policy

    @classmethod
    async def open(
        cls,
        target: PostgresTarget,
        credential: PostgresCredential,
        *,
        connect_timeout: float = 10.0,
        policy: CommandPolicyGuard | None = None,
        subject: SubjectRef | None = None,
        resource: ResourceRef | None = None,
        account_id: str = "",
        session_id: str | None = None,
        session_factory: Any = None,
        db: Any = None,
    ) -> PostgresQueryChannel:
        ssl_context = _build_ssl_context(target)
        return cls(
            target,
            credential,
            ssl_context=ssl_context,
            connect_timeout=connect_timeout,
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

    async def run_query(
        self,
        sql: str,
        sink: CommandEventSink,
        *,
        sequence: int,
    ) -> CommandEvent:
        decision = await self._policy.authorize(sql)
        if not decision.allowed:
            raise PostgresChannelError(
                "PG_COMMAND_DENIED",
                decision.reason_code,
                audit_event_id=decision.audit_event_id,
            )
        try:
            stdout, stderr, exit_code = await self._execute_simple_query(sql)
        except ssl.SSLError as exc:
            raise PostgresChannelError("PG_TLS_HANDSHAKE_FAILED", str(exc)) from exc
        except TimeoutError as exc:
            raise PostgresChannelError("PG_CONNECT_TIMEOUT", "connection timed out") from exc
        except OSError as exc:
            raise PostgresChannelError("PG_CONNECT_FAILED", str(exc)) from exc

        event = CommandEvent(
            sequence=sequence,
            command=sql,
            exit_code=exit_code,
            output_excerpt=self._policy.mask_text(_excerpt(stdout, stderr)),
        )
        await sink.emit(event)
        return event

    async def run_script(
        self,
        statements: list[str],
        sink: CommandEventSink,
        *,
        start_sequence: int = 0,
    ) -> list[CommandEvent]:
        events: list[CommandEvent] = []
        for offset, sql in enumerate(statements):
            events.append(await self.run_query(sql, sink, sequence=start_sequence + offset))
        return events

    async def close(self) -> None:
        return None

    async def _execute_simple_query(self, sql: str) -> tuple[str, str, int | None]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self._target.host,
                self._target.port,
                ssl=self._ssl,
            ),
            timeout=self._connect_timeout,
        )
        try:
            writer.write(_build_startup_message(self._target.username, self._target.database))
            await writer.drain()
            await self._complete_startup(reader, writer)
            writer.write(_pack_message(_MSG_QUERY, sql.encode("utf-8") + b"\x00"))
            await writer.drain()
            return await self._read_query_results(reader)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _complete_startup(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            msg_type, payload = await _read_message(reader)
            if msg_type == _MSG_AUTH_REQUEST:
                (auth_type,) = struct.unpack("!I", payload[:4])
                if auth_type == 0:
                    continue
                if auth_type == 3:
                    writer.write(
                        _pack_message(
                            _MSG_PASSWORD,
                            self._credential.password.encode("utf-8") + b"\x00",
                        )
                    )
                    await writer.drain()
                    continue
                raise PostgresChannelError(
                    "PG_AUTH_UNSUPPORTED",
                    f"unsupported authentication type {auth_type}",
                )
            if msg_type == _MSG_ERROR:
                raise PostgresChannelError("PG_AUTH_FAILED", _parse_error(payload))
            if msg_type == _MSG_READY:
                return

    async def _read_query_results(self, reader: asyncio.StreamReader) -> tuple[str, str, int | None]:
        rows: list[str] = []
        stderr = ""
        exit_code: int | None = 0
        complete_tag = ""
        while True:
            msg_type, payload = await _read_message(reader)
            if msg_type == _MSG_DATA_ROW:
                rows.append(_parse_data_row(payload))
            elif msg_type == _MSG_COMMAND_COMPLETE:
                exit_code, complete_tag = _parse_command_complete(payload)
            elif msg_type == _MSG_ERROR:
                stderr = _parse_error(payload)
                exit_code = 1
            elif msg_type == _MSG_READY:
                stdout = "\n".join(rows)
                if complete_tag and not rows:
                    stdout = complete_tag
                return stdout, stderr, exit_code
