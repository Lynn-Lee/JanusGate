# ruff: noqa: E402, I001
"""#t71 M3：PostgreSQL Simple Query 代理通道端到端与安全回归测试。"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from app.connectors.command_policy import CommandPolicyGuard, InMemoryCommandAuditSink
from app.connectors.postgres_proxy import (
    PostgresChannelError,
    PostgresCredential,
    PostgresQueryChannel,
    PostgresTarget,
    _pack_message,
    _read_message,
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


@dataclass
class _FakePolicy:
    effect: CommandFilterEffect = CommandFilterEffect.ALLOW
    reason: str = "COMMAND_ACCEPTED_BY_DEFAULT"

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
        return MaskingResponse(
            masked_text=request.text,
            redaction_count=0,
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


@dataclass
class _CapturedStartup:
    user: str
    database: str
    password: str | None


async def _pg_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    captured: list[_CapturedStartup],
) -> None:
    (length,) = struct.unpack("!I", await reader.readexactly(4))
    payload = await reader.readexactly(length - 4)
    idx = 4
    user = "postgres"
    database = "postgres"
    while idx < len(payload):
        end = payload.find(b"\x00", idx)
        if end == -1:
            break
        key = payload[idx:end].decode("ascii")
        idx = end + 1
        end = payload.find(b"\x00", idx)
        if end == -1:
            break
        value = payload[idx:end].decode("utf-8")
        idx = end + 1
        if key == "user":
            user = value
        elif key == "database":
            database = value

    password: str | None = None
    writer.write(_pack_message(b"R", struct.pack("!I", 3)))
    await writer.drain()
    msg_type, auth_payload = await _read_message(reader)
    if msg_type == b"p":
        password = auth_payload.rstrip(b"\x00").decode("utf-8")
    captured.append(_CapturedStartup(user=user, database=database, password=password))

    writer.write(_pack_message(b"R", struct.pack("!I", 0)))
    await writer.drain()
    writer.write(_pack_message(b"Z", b"I"))
    await writer.drain()

    while True:
        try:
            msg_type, query_payload = await _read_message(reader)
        except asyncio.IncompleteReadError:
            break
        if msg_type == b"Q":
            sql = query_payload.rstrip(b"\x00").decode("utf-8")
            if "fail" in sql.lower():
                writer.write(
                    _pack_message(
                        b"E",
                        b"S\x00ERROR\x00C\x0042501\x00M\x00permission denied\x00\x00",
                    )
                )
                await writer.drain()
                writer.write(_pack_message(b"Z", b"E"))
                await writer.drain()
                continue
            if sql.strip().upper().startswith("SELECT"):
                writer.write(
                    _pack_message(
                        b"T",
                        struct.pack("!h", 1) + b"col\x00" + struct.pack("!h", 0) + struct.pack("!I", 25) + b"\x00",
                    )
                )
                await writer.drain()
                writer.write(
                    _pack_message(
                        b"D",
                        struct.pack("!h", 1) + struct.pack("!i", 7) + b"row-one",
                    )
                )
                await writer.drain()
            writer.write(_pack_message(b"C", b"SELECT 1\x00"))
            await writer.drain()
            writer.write(_pack_message(b"Z", b"I"))
            await writer.drain()
        elif msg_type == b"X":
            break
    writer.close()
    await writer.wait_closed()


@pytest.fixture
async def pg_server() -> AsyncIterator[tuple[str, int, list[_CapturedStartup]]]:
    captured: list[_CapturedStartup] = []
    server = await asyncio.start_server(
        lambda r, w: _pg_handler(r, w, captured=captured),
        host="127.0.0.1",
        port=0,
    )
    host, port = server.sockets[0].getsockname()[:2]
    try:
        yield host, port, captured
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_postgres_run_query_emits_command_event(pg_server) -> None:
    host, port, captured = pg_server
    sink = _RecordingSink()
    channel = await PostgresQueryChannel.open(
        PostgresTarget(host=host, port=port, database="appdb", username="alice"),
        PostgresCredential(password="s3cret"),
        policy=_guard(_FakePolicy()),
    )
    event = await channel.run_query("SELECT 1", sink, sequence=1)
    assert event.command == "SELECT 1"
    assert event.exit_code == 0
    assert "row-one" in event.output_excerpt
    assert sink.events[0] is event
    assert captured[0].user == "alice"
    assert captured[0].database == "appdb"
    assert captured[0].password == "s3cret"


@pytest.mark.asyncio
async def test_postgres_password_not_in_credential_repr() -> None:
    cred = PostgresCredential(password="top-secret")
    assert "top-secret" not in repr(cred)


@pytest.mark.asyncio
async def test_postgres_command_denied_before_connect(pg_server) -> None:
    host, port, _ = pg_server

    class DenyPolicy(_FakePolicy):
        def __init__(self) -> None:
            super().__init__(effect=CommandFilterEffect.DENY, reason="SQL_DENIED")

    channel = await PostgresQueryChannel.open(
        PostgresTarget(host=host, port=port, database="appdb", username="alice"),
        PostgresCredential(password="s3cret"),
        policy=_guard(DenyPolicy()),
    )
    sink = _RecordingSink()
    with pytest.raises(PostgresChannelError, match="PG_COMMAND_DENIED"):
        await channel.run_query("SELECT 1", sink, sequence=1)
    assert sink.events == []


@pytest.mark.asyncio
async def test_postgres_error_response_maps_nonzero_exit(pg_server) -> None:
    host, port, _ = pg_server
    sink = _RecordingSink()
    channel = await PostgresQueryChannel.open(
        PostgresTarget(host=host, port=port, database="appdb", username="alice"),
        PostgresCredential(password="s3cret"),
        policy=_guard(_FakePolicy()),
    )
    event = await channel.run_query("SELECT fail", sink, sequence=2)
    assert event.exit_code == 1
    assert "permission denied" in event.output_excerpt


@pytest.mark.asyncio
async def test_postgres_tls_required_without_ca_rejects() -> None:
    with pytest.raises(PostgresChannelError, match="PG_TLS_CA_MISSING"):
        await PostgresQueryChannel.open(
            PostgresTarget(
                host="127.0.0.1",
                port=5432,
                database="appdb",
                username="alice",
                require_tls=True,
            ),
            PostgresCredential(password="s3cret"),
            policy=_guard(_FakePolicy()),
        )
