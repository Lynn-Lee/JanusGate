"""连接器侧 MySQL COM_QUERY 代理通道（#t71 M3：数据库协议代理切片 2）。

实现 MySQL 4.1+ 线协议的 **COM_QUERY**（``0x03``）子集与 ``mysql_native_password`` 认证，
把每条 SQL 映射为对齐 #t46 命令事件管线的 :class:`~app.connectors.ssh_channel.CommandEvent`。
纯 Python ``asyncio`` 实现，不 fork ``mysql`` 客户端、不依赖 ``mysqlclient`` / ``PyMySQL``。

安全约束由 ``tests/connectors/test_mysql_proxy.py`` 证明，与 PostgreSQL 切片对齐。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ssl
import struct
from dataclasses import dataclass, field
from typing import Any

from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.connectors.db_channel_errors import DbChannelError, merge_output_excerpt
from app.connectors.ssh_channel import CommandEvent, CommandEventSink
from app.policy.schemas import ResourceRef, SubjectRef

PROTOCOL = "mysql"

_COM_QUERY = 0x03
_CLIENT_PROTOCOL_41 = 512
_CLIENT_SECURE_CONNECTION = 32768
_CLIENT_CONNECT_WITH_DB = 8
_CLIENT_MULTI_RESULTS = 131072


class MysqlChannelError(DbChannelError):
    """MySQL 代理通道错误（稳定错误码前缀 ``MYSQL_`` / ``PG_`` 共享 ``DbChannelError``）。"""


@dataclass(frozen=True)
class MysqlTarget:
    host: str
    port: int
    database: str
    username: str
    require_tls: bool = False
    server_ca: str | None = None


@dataclass(frozen=True)
class MysqlCredential:
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.password:
            raise MysqlChannelError(
                "MYSQL_CREDENTIAL_MISSING",
                "must supply an in-memory database password",
            )

    def __repr__(self) -> str:  # pragma: no cover
        return "MysqlCredential(password=<redacted>)"


def _mysql_native_password_scramble(password: str, salt: bytes) -> bytes:
    stage1 = hashlib.sha1(password.encode("utf-8")).digest()
    stage2 = hashlib.sha1(stage1).digest()
    stage3 = hashlib.sha1(stage2 + salt).digest()
    return bytes(s1 ^ s3 for s1, s3 in zip(stage1, stage3))


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = await reader.readexactly(4)
    length = header[0] | (header[1] << 8) | (header[2] << 16)
    sequence = header[3]
    payload = await reader.readexactly(length)
    return sequence, payload


async def _write_packet(writer: asyncio.StreamWriter, sequence: int, payload: bytes) -> None:
    length = len(payload)
    header = bytes([length & 0xFF, (length >> 8) & 0xFF, (length >> 16) & 0xFF, sequence & 0xFF])
    writer.write(header + payload)
    await writer.drain()


def _read_lenenc_int(data: bytes, idx: int = 0) -> tuple[int, int]:
    first = data[idx]
    if first < 0xFB:
        return first, idx + 1
    if first == 0xFC:
        return struct.unpack_from("<H", data, idx + 1)[0], idx + 3
    if first == 0xFD:
        value = data[idx + 1] | (data[idx + 2] << 8) | (data[idx + 3] << 16)
        return value, idx + 4
    if first == 0xFE:
        return struct.unpack_from("<Q", data, idx + 1)[0], idx + 9
    raise MysqlChannelError("MYSQL_PROTOCOL_INVALID", "invalid length-encoded integer")


def _read_lenenc_string(data: bytes, idx: int = 0) -> tuple[str, int]:
    length, next_idx = _read_lenenc_int(data, idx)
    raw = data[next_idx : next_idx + length]
    return raw.decode("utf-8", errors="replace"), next_idx + length


def _parse_err_packet(payload: bytes) -> str:
    if not payload or payload[0] != 0xFF:
        return "query failed"
    idx = 3
    if len(payload) > 3 and payload[3:4] == b"#":
        idx = 9
    message = payload[idx:].decode("utf-8", errors="replace")
    return message or "query failed"


def _parse_text_row(payload: bytes) -> str:
    values: list[str] = []
    idx = 0
    while idx < len(payload):
        if payload[idx] == 0xFB and idx == 0:
            break
        value, idx = _read_lenenc_string(payload, idx)
        values.append(value)
    return "\t".join(values)


def _build_ssl_context(target: MysqlTarget) -> ssl.SSLContext | None:
    if not target.require_tls:
        return None
    ca = (target.server_ca or "").strip()
    if not ca:
        raise MysqlChannelError(
            "MYSQL_TLS_CA_MISSING",
            "target requires TLS but has no trusted server CA; refusing to trust on first use",
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_verify_locations(cadata=ca)
    except ssl.SSLError as exc:
        raise MysqlChannelError("MYSQL_TLS_CA_INVALID", str(exc)) from exc
    return context


def _parse_handshake_salt(payload: bytes) -> bytes:
    idx = 1
    nul = payload.index(b"\x00", idx)
    idx = nul + 1
    idx += 4
    salt1 = payload[idx : idx + 8]
    idx += 8
    idx += 1
    idx += 2 + 1 + 2 + 2
    auth_data_len = payload[idx]
    idx += 1
    idx += 10
    salt2_len = max(13, auth_data_len - 8)
    salt2 = payload[idx : idx + salt2_len]
    salt = (salt1 + salt2)[:20]
    return salt.ljust(20, b"\x00")


class MysqlQueryChannel:
    """已授权的 MySQL COM_QUERY 通道。"""

    def __init__(
        self,
        target: MysqlTarget,
        credential: MysqlCredential,
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
        target: MysqlTarget,
        credential: MysqlCredential,
        *,
        connect_timeout: float = 10.0,
        policy: CommandPolicyGuard | None = None,
        subject: SubjectRef | None = None,
        resource: ResourceRef | None = None,
        account_id: str = "",
        session_id: str | None = None,
        session_factory: Any = None,
        db: Any = None,
    ) -> MysqlQueryChannel:
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
            raise MysqlChannelError(
                "MYSQL_COMMAND_DENIED",
                decision.reason_code,
                audit_event_id=decision.audit_event_id,
            )
        try:
            stdout, stderr, exit_code = await self._execute_query(sql)
        except ssl.SSLError as exc:
            raise MysqlChannelError("MYSQL_TLS_HANDSHAKE_FAILED", str(exc)) from exc
        except TimeoutError as exc:
            raise MysqlChannelError("MYSQL_CONNECT_TIMEOUT", "connection timed out") from exc
        except OSError as exc:
            raise MysqlChannelError("MYSQL_CONNECT_FAILED", str(exc)) from exc

        event = CommandEvent(
            sequence=sequence,
            command=sql,
            exit_code=exit_code,
            output_excerpt=self._policy.mask_text(merge_output_excerpt(stdout, stderr)),
        )
        await sink.emit(event)
        return event

    async def close(self) -> None:
        return None

    async def _execute_query(self, sql: str) -> tuple[str, str, int | None]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._target.host, self._target.port, ssl=self._ssl),
            timeout=self._connect_timeout,
        )
        try:
            await self._complete_handshake(reader, writer)
            await _write_packet(writer, 0, bytes([_COM_QUERY]) + sql.encode("utf-8"))
            return await self._read_query_results(reader)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _complete_handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        _, payload = await _read_packet(reader)
        if len(payload) < 44:
            raise MysqlChannelError("MYSQL_HANDSHAKE_INVALID", "handshake packet too short")
        salt = _parse_handshake_salt(payload)

        capabilities = (
            _CLIENT_PROTOCOL_41
            | _CLIENT_SECURE_CONNECTION
            | _CLIENT_CONNECT_WITH_DB
            | _CLIENT_MULTI_RESULTS
        )
        response = bytearray()
        response += struct.pack("<I", capabilities)
        response += struct.pack("<I", 16777216)
        response += bytes([33])
        response += bytes(23)
        response += self._target.username.encode("utf-8") + b"\x00"
        scrambled = _mysql_native_password_scramble(self._credential.password, salt)
        response += bytes([len(scrambled)]) + scrambled
        response += self._target.database.encode("utf-8") + b"\x00"
        await _write_packet(writer, 1, bytes(response))

        _, auth_payload = await _read_packet(reader)
        if auth_payload and auth_payload[0] == 0xFF:
            raise MysqlChannelError("MYSQL_AUTH_FAILED", _parse_err_packet(auth_payload))
        if auth_payload and auth_payload[0] == 0xFE:
            raise MysqlChannelError(
                "MYSQL_AUTH_PLUGIN_UNSUPPORTED",
                "unsupported auth switch request",
            )

    async def _read_query_results(self, reader: asyncio.StreamReader) -> tuple[str, str, int | None]:
        _, payload = await _read_packet(reader)
        if not payload:
            raise MysqlChannelError("MYSQL_PROTOCOL_INVALID", "empty query response")
        if payload[0] == 0xFF:
            return "", _parse_err_packet(payload), 1
        if payload[0] == 0x00:
            tag = payload[5:].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            return tag or "OK", "", 0

        column_count, _ = _read_lenenc_int(payload, 0)
        for _ in range(column_count):
            await _read_packet(reader)
        rows: list[str] = []
        while True:
            _, row_payload = await _read_packet(reader)
            if not row_payload:
                break
            if row_payload[0] == 0xFF:
                return "", _parse_err_packet(row_payload), 1
            if row_payload[0] == 0xFE and len(row_payload) < 9:
                break
            if row_payload[0] == 0x00:
                tag = row_payload[5:].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
                stdout = "\n".join(rows)
                return stdout or tag or "OK", "", 0
            rows.append(_parse_text_row(row_payload))
        return "\n".join(rows), "", 0
