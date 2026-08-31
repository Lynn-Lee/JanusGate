# ruff: noqa: E402, I001
"""#t69：交互式 PTY 通道与命令流解析测试。

- 解析器用例为纯单元测试，无 SSH。
- 会话用例基于进程内 asyncssh 交互式 shell，自包含、无外部依赖、确定性运行。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncssh
import pytest

from app.connectors.ssh_channel import CommandEvent, SshChannelError, SshCredential, SshTarget
from app.connectors.ssh_interactive import (
    DEFAULT_PROMPT,
    InteractiveCommandParser,
    SshInteractiveSession,
)

# --- 纯解析器：从键盘输入流重建命令 ------------------------------------------------


def test_parser_reconstructs_simple_commands() -> None:
    parser = InteractiveCommandParser()
    assert parser.feed("whoami\r") == ["whoami"]
    assert parser.feed("ls -la\n") == ["ls -la"]


def test_parser_handles_multiple_and_partial_commands() -> None:
    parser = InteractiveCommandParser()
    assert parser.feed("id\rpwd\r") == ["id", "pwd"]
    # 半条命令跨多次 feed。
    assert parser.feed("ec") == []
    assert parser.feed("ho hi\r") == ["echo hi"]


def test_parser_crlf_counts_as_single_terminator() -> None:
    parser = InteractiveCommandParser()
    assert parser.feed("uname\r\n") == ["uname"]
    # \r 与其后的 \n 属同一终止符，不产出空命令。
    assert parser.feed("date\r\nls\r\n") == ["date", "ls"]


def test_parser_backspace_deletes_last_char() -> None:
    parser = InteractiveCommandParser()
    # 键入 "sl" 退格删 "l" 再输入 "udo" → "sudo"。
    assert parser.feed("sl\x7fudo\r") == ["sudo"]
    assert parser.feed("abc\x08\x08x\r") == ["ax"]


def test_parser_ctrl_u_clears_line_and_ctrl_c_cancels() -> None:
    parser = InteractiveCommandParser()
    # Ctrl-U 清空已输入部分。
    assert parser.feed("rm -rf /\x15ls\r") == ["ls"]
    # Ctrl-C 取消当前行：该行不作为命令产出。
    assert parser.feed("dangerous\x03") == []
    assert parser.feed("safe\r") == ["safe"]


def test_parser_strips_ansi_escape_sequences() -> None:
    parser = InteractiveCommandParser()
    # 方向键等 CSI 序列（ESC [ C 等）应被丢弃，不污染命令。
    assert parser.feed("ls\x1b[C\x1b[Dpwd\r") == ["lspwd"]
    # 颜色类 CSI（ESC [ 3 1 m）同样丢弃。
    assert parser.feed("\x1b[31mred\x1b[0m\r") == ["red"]


def test_parser_ignores_blank_lines() -> None:
    parser = InteractiveCommandParser()
    assert parser.feed("\r\n\r\n") == []
    assert parser.feed("   \r") == []


# --- 交互式 PTY 会话：进程内 shell 端到端 ----------------------------------------


@dataclass
class _RecordingSink:
    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


async def _interactive_handler(process: asyncssh.SSHServerProcess) -> None:
    """最小交互式 shell：写 prompt，对每条命令产出一行确定性输出。

    注意：PTY 会自动回显操作员输入，handler 不再重复回显命令，只写命令输出与下一个 prompt
    （与真实 shell 一致，命令行回显由 PTY 负责）。
    """

    process.stdout.write(DEFAULT_PROMPT)
    async for line in process.stdin:
        command = line.rstrip("\n").rstrip("\r")
        if command == "exit":
            break
        process.stdout.write(f"out:{command}\n{DEFAULT_PROMPT}")
    process.exit(0)


@dataclass
class _RunningServer:
    acceptor: asyncssh.SSHAcceptor
    host: str
    port: int
    host_public_key: str
    client_private_key: bytes


@pytest.fixture
async def server() -> AsyncIterator[_RunningServer]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())
    acceptor = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=_interactive_handler,
    )
    running = _RunningServer(
        acceptor=acceptor,
        host="127.0.0.1",
        port=acceptor.get_port(),
        host_public_key=host_key.export_public_key().decode().strip(),
        client_private_key=client_key.export_private_key(),
    )
    try:
        yield running
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


def _target(running: _RunningServer) -> SshTarget:
    return SshTarget(
        host=running.host,
        port=running.port,
        username="janus",
        trusted_host_key=running.host_public_key,
    )


async def test_interactive_session_runs_commands_and_emits_events(server: _RunningServer) -> None:
    sink = _RecordingSink()
    credential = SshCredential(private_key=server.client_private_key)

    session = await SshInteractiveSession.open(_target(server), credential, sink, start_sequence=1)
    try:
        first = await session.run_command("whoami")
        second = await session.run_command("ls -la")
    finally:
        await session.close()

    # 命令按序解析，输出剥离了回显的命令行本身。
    assert first.command == "whoami"
    assert first.sequence == 1
    assert first.output_excerpt == "out:whoami"
    assert first.exit_code is None
    assert second.command == "ls -la"
    assert second.sequence == 2
    assert second.output_excerpt == "out:ls -la"
    assert [e.command for e in sink.events] == ["whoami", "ls -la"]


async def test_interactive_session_parses_edited_command_line(server: _RunningServer) -> None:
    sink = _RecordingSink()
    credential = SshCredential(private_key=server.client_private_key)

    session = await SshInteractiveSession.open(_target(server), credential, sink)
    try:
        # 含退格的原始键入：解析器还原为规范命令 "sudo id"。
        event = await session.run_command("sl\x7fudo id")
    finally:
        await session.close()

    assert event.command == "sudo id"


async def test_interactive_session_as_context_manager_closes(server: _RunningServer) -> None:
    sink = _RecordingSink()
    credential = SshCredential(private_key=server.client_private_key)

    async with await SshInteractiveSession.open(_target(server), credential, sink) as session:
        await session.run_command("id")

    assert [e.command for e in sink.events] == ["id"]


async def test_interactive_session_open_rejects_untrusted_host_key(server: _RunningServer) -> None:
    wrong_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    sink = _RecordingSink()
    credential = SshCredential(private_key=server.client_private_key)

    with pytest.raises(SshChannelError) as exc_info:
        await SshInteractiveSession.open(
            SshTarget(
                host=server.host,
                port=server.port,
                username="janus",
                trusted_host_key=wrong_key,
            ),
            credential,
            sink,
        )

    assert exc_info.value.code == "SSH_HOST_KEY_REJECTED"
    assert sink.events == []


async def _silent_after_banner_handler(process: asyncssh.SSHServerProcess) -> None:
    """先写首个 prompt（让 open 就绪），随后吞掉输入且不再产出任何 prompt。"""

    process.stdout.write(DEFAULT_PROMPT)
    async for _line in process.stdin:
        pass  # 不响应，模拟目标 shell 卡死不吐 prompt
    process.exit(0)


async def test_run_command_times_out_when_prompt_never_arrives() -> None:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())
    acceptor = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=_silent_after_banner_handler,
    )
    target = SshTarget(
        host="127.0.0.1",
        port=acceptor.get_port(),
        username="janus",
        trusted_host_key=host_key.export_public_key().decode().strip(),
    )
    sink = _RecordingSink()
    try:
        session = await SshInteractiveSession.open(
            target,
            SshCredential(private_key=client_key.export_private_key()),
            sink,
            read_timeout=0.3,
        )
        try:
            with pytest.raises(SshChannelError) as exc_info:
                await session.run_command("hang")
        finally:
            await session.close()
    finally:
        acceptor.close()
        await acceptor.wait_closed()

    assert exc_info.value.code == "SSH_INTERACTIVE_READ_TIMEOUT"
    assert sink.events == []

# --- #t65 执行前策略守卫 -------------------------------------------------------

from app.connectors.command_policy import CommandPolicyGuard, InMemoryCommandAuditSink
from app.models.acl import CommandFilterAction
from app.policy.schemas import (
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingResponse,
    ResourceRef,
    SubjectRef,
)


class _T65FakePolicy:
    def __init__(self, *, effect=CommandFilterEffect.ALLOW, reason="COMMAND_ACCEPTED_BY_DEFAULT"):
        self.effect = effect
        self.reason = reason
        self.seen: list[str] = []

    def evaluate_command(self, request):
        self.seen.append(request.command)
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


def _t65_guard(policy: _T65FakePolicy, sink: InMemoryCommandAuditSink) -> CommandPolicyGuard:
    return CommandPolicyGuard(
        policy,
        subject=SubjectRef(id="u1", tenant_id="t1"),
        resource=ResourceRef(id="a1", type="asset", tenant_id="t1"),
        account_id="acct",
        audit_sink=sink,
    )

async def test_interactive_reject_after_rebuild_before_remote(server: _RunningServer) -> None:
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()
    audit = InMemoryCommandAuditSink()
    policy = _T65FakePolicy(effect=CommandFilterEffect.DENY, reason="COMMAND_REJECT")
    session = await SshInteractiveSession.open(
        _target(server), credential, sink, policy=_t65_guard(policy, audit)
    )
    try:
        with pytest.raises(SshChannelError) as excinfo:
            await session.run_command("rm -rf /")
        assert excinfo.value.code == "SSH_COMMAND_DENIED"
        assert excinfo.value.audit_event_id == audit.events[0]["id"]
        assert sink.events == []
        assert policy.seen == ["rm -rf /"]
    finally:
        await session.close()



from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app


@pytest.fixture
async def acl_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _install_acl_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


def _seed_reject_rm_via_crud(*, tenant_id: str = "tenant-a") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": ["admin"],
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/command-filter-acls/",
            json={
                "name": "deny-rm",
                "priority": 10,
                "action": "reject",
                "command_groups": [
                    {"name": "danger", "match_type": "command", "patterns": ["rm"]}
                ],
            },
        )
    assert created.status_code == 201, created.text


async def test_default_assembly_tenant_acl_interactive_does_not_reach_remote(
    server: _RunningServer,
    acl_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _install_acl_db(acl_session_factory)
    _seed_reject_rm_via_crud(tenant_id="tenant-a")
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()
    session = await SshInteractiveSession.open(
        _target(server),
        credential,
        sink,
        subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-a"),
        account_id="root",
        session_factory=acl_session_factory,
    )
    try:
        with pytest.raises(SshChannelError) as excinfo:
            await session.run_command("rm -rf /")
        assert excinfo.value.code == "SSH_COMMAND_DENIED"
        assert sink.events == []
    finally:
        await session.close()
