# ruff: noqa: E402, I001
"""#t71 MySQL COM_QUERY 代理通道端到端与安全回归测试。"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
import struct
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.connectors.command_policy import CommandPolicyGuard, InMemoryCommandAuditSink
from app.connectors.mysql_proxy import (
    MysqlChannelError,
    MysqlCredential,
    MysqlQueryChannel,
    MysqlTarget,
    _mysql_native_password_scramble,
    _read_packet,
    _write_packet,
)
from app.connectors.ssh_channel import CommandEvent
from app.models.acl import CommandFilterAction
from app.policy.schemas import (
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingResponse,
    ResourceRef,
    SubjectRef,
)

_TEST_SALT = b"012345678901234567890"[:20]


@dataclass
class _FakePolicy:
    effect: CommandFilterEffect = CommandFilterEffect.ALLOW
    reason: str = "COMMAND_ACCEPTED_BY_DEFAULT"
    mask_replace: tuple[str, str] | None = None

    def evaluate_command(self, request):
        return CommandDecisionResponse(
            effect=self.effect,
            action=(
                CommandFilterAction.REJECT
                if self.effect is CommandFilterEffect.DENY
                else CommandFilterAction.ACCEPT
            ),
            reason_code=self.reason,
            explain_trace=["test"],
            audit_event_id="pde_test",
        )

    def mask(self, request):
        text = request.text
        if self.mask_replace is not None:
            text = text.replace(self.mask_replace[0], self.mask_replace[1])
        return MaskingResponse(
            masked_text=text,
            redaction_count=1 if self.mask_replace else 0,
            explain_trace=[],
            audit_event_id="pde_mask",
        )


def _guard(policy: _FakePolicy) -> CommandPolicyGuard:
    return CommandPolicyGuard(
        policy,
        subject=SubjectRef(id="u1", tenant_id="t1"),
        resource=ResourceRef(id="a1", type="asset", tenant_id="t1"),
        account_id="acct",
        audit_sink=InMemoryCommandAuditSink(),
    )


@dataclass
class _RecordingSink:
    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


def _self_signed_cert() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def _server_ssl_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem") as cert_file:
        cert_file.write(cert_pem)
        cert_path = cert_file.name
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem") as key_file:
        key_file.write(key_pem)
        key_path = key_file.name
    context.load_cert_chain(cert_path, key_path)
    Path(cert_path).unlink(missing_ok=True)
    Path(key_path).unlink(missing_ok=True)
    return context


def _build_handshake_payload() -> bytes:
    payload = bytearray()
    payload.append(10)
    payload.extend(b"5.7.0-test\x00")
    payload.extend(struct.pack("<I", 1))
    payload.extend(_TEST_SALT[:8])
    payload.append(0)
    payload.extend(struct.pack("<H", 0xF7DF))
    payload.append(33)
    payload.extend(struct.pack("<H", 0x0002))
    payload.extend(struct.pack("<H", 0x81FF))
    payload.append(21)
    payload.extend(bytes(10))
    payload.extend(_TEST_SALT[8:])
    payload.extend(b"\x00")
    payload.extend(b"mysql_native_password\x00")
    return bytes(payload)


def _build_ok_packet() -> bytes:
    return bytes([0x00, 0x00, 0x00, 0x02, 0x00, 0x00])


def _build_eof_packet() -> bytes:
    return bytes([0xFE, 0x00, 0x00, 0x02, 0x00])


def _build_err_packet(message: str) -> bytes:
    encoded = message.encode("utf-8")
    return bytes([0xFF, 0x01, 0x00, ord("#")]) + b"42000" + encoded


async def _mysql_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    _, auth_payload = await _read_packet(reader)
    if len(auth_payload) < 36:
        writer.close()
        return
    username_end = auth_payload.index(b"\x00", 32)
    _ = auth_payload[32:username_end].decode("utf-8")
    auth_len = auth_payload[username_end + 1]
    auth_start = username_end + 2
    client_scramble = auth_payload[auth_start : auth_start + auth_len]
    expected = _mysql_native_password_scramble("s3cret", _TEST_SALT)
    if client_scramble != expected:
        await _write_packet(writer, 2, _build_err_packet("access denied"))
        writer.close()
        return
    await _write_packet(writer, 2, _build_ok_packet())

    while True:
        try:
            _, query_payload = await _read_packet(reader)
        except asyncio.IncompleteReadError:
            break
        if not query_payload or query_payload[0] != 0x03:
            break
        sql = query_payload[1:].decode("utf-8")
        if "fail" in sql.lower():
            await _write_packet(writer, 1, _build_err_packet("permission denied"))
            continue
        if sql.strip().upper().startswith("SELECT"):
            await _write_packet(writer, 1, bytes([1]))
            await _write_packet(writer, 2, bytes([3, 0, 0, 0, 3, 0, 0, 0]) + b"col")
            row = bytes([0x07]) + b"row-one"
            await _write_packet(writer, 3, row)
            await _write_packet(writer, 4, _build_eof_packet())
        else:
            await _write_packet(writer, 1, b"OK\x00")
    writer.close()
    await writer.wait_closed()


async def _mysql_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await _write_packet(writer, 0, _build_handshake_payload())
    await _mysql_session(reader, writer)


@pytest.fixture
async def mysql_server() -> AsyncIterator[tuple[str, int]]:
    server = await asyncio.start_server(_mysql_handler, host="127.0.0.1", port=0)
    host, port = server.sockets[0].getsockname()[:2]
    try:
        yield host, port
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_mysql_run_query_emits_command_event(mysql_server) -> None:
    host, port = mysql_server
    sink = _RecordingSink()
    channel = await MysqlQueryChannel.open(
        MysqlTarget(host=host, port=port, database="appdb", username="alice"),
        MysqlCredential(password="s3cret"),
        policy=_guard(_FakePolicy()),
    )
    event = await channel.run_query("SELECT 1", sink, sequence=1)
    assert event.command == "SELECT 1"
    assert event.exit_code == 0
    assert "row-one" in event.output_excerpt


@pytest.mark.asyncio
async def test_mysql_password_not_in_credential_repr() -> None:
    cred = MysqlCredential(password="top-secret")
    assert "top-secret" not in repr(cred)


@pytest.mark.asyncio
async def test_mysql_command_denied_before_connect(mysql_server) -> None:
    host, port = mysql_server
    channel = await MysqlQueryChannel.open(
        MysqlTarget(host=host, port=port, database="appdb", username="alice"),
        MysqlCredential(password="s3cret"),
        policy=_guard(_FakePolicy(effect=CommandFilterEffect.DENY, reason="SQL_DENIED")),
    )
    sink = _RecordingSink()
    with pytest.raises(MysqlChannelError, match="MYSQL_COMMAND_DENIED"):
        await channel.run_query("SELECT 1", sink, sequence=1)
    assert sink.events == []


@pytest.mark.asyncio
async def test_mysql_error_response_maps_nonzero_exit(mysql_server) -> None:
    host, port = mysql_server
    sink = _RecordingSink()
    channel = await MysqlQueryChannel.open(
        MysqlTarget(host=host, port=port, database="appdb", username="alice"),
        MysqlCredential(password="s3cret"),
        policy=_guard(_FakePolicy()),
    )
    event = await channel.run_query("SELECT fail", sink, sequence=2)
    assert event.exit_code == 1
    assert "permission denied" in event.output_excerpt


@pytest.mark.asyncio
async def test_mysql_query_result_is_masked(mysql_server) -> None:
    host, port = mysql_server
    channel = await MysqlQueryChannel.open(
        MysqlTarget(host=host, port=port, database="appdb", username="alice"),
        MysqlCredential(password="s3cret"),
        policy=_guard(_FakePolicy(mask_replace=("row-one", "***"))),
    )
    event = await channel.run_query("SELECT 1", _RecordingSink(), sequence=1)
    assert "***" in event.output_excerpt
    assert "row-one" not in event.output_excerpt


@pytest.mark.asyncio
async def test_mysql_tls_required_without_ca_rejects() -> None:
    with pytest.raises(MysqlChannelError, match="MYSQL_TLS_CA_MISSING"):
        await MysqlQueryChannel.open(
            MysqlTarget(
                host="127.0.0.1",
                port=3306,
                database="appdb",
                username="alice",
                require_tls=True,
            ),
            MysqlCredential(password="s3cret"),
            policy=_guard(_FakePolicy()),
        )


@pytest.mark.asyncio
async def test_mysql_tls_query_with_pinned_ca() -> None:
    cert_pem, key_pem = _self_signed_cert()
    server_ssl = _server_ssl_context(cert_pem, key_pem)

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _write_packet(writer, 0, _build_handshake_payload())
        _, ssl_payload = await _read_packet(reader)
        capabilities = struct.unpack_from("<I", ssl_payload, 0)[0]
        assert capabilities & 2048
        await writer.start_tls(server_ssl)
        await _mysql_session(reader, writer)

    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    host, port = server.sockets[0].getsockname()[:2]
    try:
        channel = await MysqlQueryChannel.open(
            MysqlTarget(
                host=host,
                port=port,
                database="appdb",
                username="alice",
                require_tls=True,
                server_ca=cert_pem,
            ),
            MysqlCredential(password="s3cret"),
            policy=_guard(_FakePolicy()),
        )
        event = await asyncio.wait_for(
            channel.run_query("SELECT 1", _RecordingSink(), sequence=1),
            timeout=10,
        )
        assert event.exit_code == 0
        assert "row-one" in event.output_excerpt
    finally:
        server.close()
        await server.wait_closed()
