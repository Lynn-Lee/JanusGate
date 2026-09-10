"""连接器侧 PostgreSQL Simple Query 代理通道（#t71 M3：数据库协议代理）。

实现 PostgreSQL 前端/后端协议的 **Simple Query**（``Q`` 消息）子集与明文密码认证
（AuthenticationCleartextPassword），把每条 SQL 映射为对齐 #t46 命令事件管线的
:class:`~app.connectors.ssh_channel.CommandEvent`。纯 Python ``asyncio`` 实现，不 fork
``psql``、不依赖 ``psycopg`` / ``asyncpg``。

安全约束由 ``tests/connectors/test_postgres_proxy.py`` 证明关闭：

- **SQL 执行前策略**：``DENY`` / ``REVIEW`` 在建连之前阻断，远端收不到查询。
- **TLS 强校验**：``require_tls=True`` 时先发 SSLRequest，再 ``start_tls``；必须预置 CA，
  ``check_hostname`` + ``CERT_REQUIRED``，拒绝 TOFU。
- **凭据仅内存**：密码不经命令行 / URL；:class:`PostgresCredential` 的 ``repr`` 屏蔽密码。
- **结果脱敏**：查询输出经 :meth:`CommandPolicyGuard.mask_text`（#t65 ``PolicyDecisionService.mask``）。
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import ssl
import struct
from dataclasses import dataclass, field
from typing import Any

from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.connectors.db_channel_errors import DbChannelError, merge_output_excerpt
from app.connectors.ssh_channel import CommandEvent, CommandEventSink
from app.policy.schemas import ResourceRef, SubjectRef

PROTOCOL = "postgresql"

_PROTOCOL_VERSION = 196608
_SSL_REQUEST_CODE = 80877103
_MSG_AUTH_REQUEST = b"R"
_MSG_PASSWORD = b"p"
_MSG_QUERY = b"Q"
_MSG_DATA_ROW = b"D"
_MSG_COMMAND_COMPLETE = b"C"
_MSG_ERROR = b"E"
_MSG_READY = b"Z"


class PostgresChannelError(DbChannelError):
    """PostgreSQL 代理通道错误（稳定错误码前缀 ``PG_``）。"""


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

    def __repr__(self) -> str:  # pragma: no cover - 简单的敏感信息屏蔽
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
    return 0, tag


def _tls_server_hostname(host: str) -> str | None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return host


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
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_verify_locations(cadata=ca)
    except ssl.SSLError as exc:
        raise PostgresChannelError("PG_TLS_CA_INVALID", str(exc)) from exc
    return context


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
        """准备通道：校验 TLS 约束并解析策略守卫；真正的 TCP 连接在执行查询时建立。

        :raises PostgresChannelError: 缺 CA（``PG_TLS_CA_MISSING``）或 CA 无法解析。
        """

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
        """执行一条 Simple Query：先策略判定，再连远端，结果脱敏后投递命令事件。"""

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
            output_excerpt=self._policy.mask_text(merge_output_excerpt(stdout, stderr)),
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
            asyncio.open_connection(self._target.host, self._target.port),
            timeout=self._connect_timeout,
        )
        try:
            if self._ssl is not None:
                writer.write(struct.pack("!II", 8, _SSL_REQUEST_CODE))
                await writer.drain()
                response = await reader.readexactly(1)
                if response != b"S":
                    raise PostgresChannelError(
                        "PG_TLS_NOT_AVAILABLE",
                        "server refused SSLRequest; refusing plaintext fallback",
                    )
                await writer.start_tls(
                    self._ssl,
                    server_hostname=_tls_server_hostname(self._target.host),
                )
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

    async def _read_query_results(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, int | None]:
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
