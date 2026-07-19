"""#t69：连接器会话运行时与网关接线测试。

- 运行时/调度器用例基于进程内 asyncssh 服务器，验证 dispatch 打开真实通道、release 关闭。
- 网关集成用例把 ConnectorRuntimeScheduler 装进 SessionGatewayService，验证完整
  create_session → ACTIVE 会真正建立连接器会话，close 会释放它。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncssh
import pytest

from app.api.sessions.service import (
    ConnectorDispatchRequest,
    InMemoryConnectionTokenStore,
    JitGrantSessionBinding,
    SessionGatewayService,
    SessionStatus,
)
from app.connectors.session_runtime import (
    ConnectorRuntimeScheduler,
    ConnectorSessionMode,
    ConnectorSessionRuntime,
    InMemorySessionConnectionResolver,
    SessionConnectionSpec,
)
from app.connectors.ssh_channel import CommandEvent, SshChannel, SshCredential, SshTarget


@dataclass
class _RecordingSink:
    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


async def _echo_handler(process: asyncssh.SSHServerProcess) -> None:
    process.stdout.write(f"executed:{process.command or '<login>'}")
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
        process_factory=_echo_handler,
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


def _resolver_for(server: _RunningServer, *, mode: ConnectorSessionMode) -> InMemorySessionConnectionResolver:
    resolver = InMemorySessionConnectionResolver()
    resolver.register(
        tenant_id="default",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        spec=SessionConnectionSpec(
            mode=mode,
            target=SshTarget(
                host=server.host,
                port=server.port,
                username="janus",
                trusted_host_key=server.host_public_key,
            ),
            credential=SshCredential(private_key=server.client_private_key),
        ),
    )
    return resolver


def _dispatch_request(session_id: str = "sess-1") -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id=session_id,
        connector_id="conn-1",
        tenant_id="default",
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
    )


# --- 运行时 / 调度器：dispatch 打开真实通道，release 关闭 -------------------------


async def test_runtime_opens_real_exec_channel_and_closes(server: _RunningServer) -> None:
    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC),
        id_factory=lambda: "cs-fixed",
    )

    record = await runtime.open(_dispatch_request())
    assert record.connector_session_id == "cs-fixed"
    assert record.mode is ConnectorSessionMode.EXEC
    assert runtime.active_ids() == ["cs-fixed"]

    # 通道是真实可用的：跑一条命令。
    sink = _RecordingSink()
    assert isinstance(record.channel, SshChannel)
    event = await record.channel.run_command("whoami", sink, sequence=0)
    assert event.output_excerpt == "executed:whoami"

    await runtime.close("cs-fixed")
    assert runtime.active_ids() == []


async def test_scheduler_dispatch_and_release(server: _RunningServer) -> None:
    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC),
        id_factory=lambda: "cs-9",
    )
    scheduler = ConnectorRuntimeScheduler(runtime)

    result = await scheduler.dispatch(_dispatch_request())
    assert result["connector_session_id"] == "cs-9"
    assert result["connection_url"] == "connector-runtime://cs-9"
    assert runtime.active_ids() == ["cs-9"]

    await scheduler.release("cs-9")
    assert runtime.active_ids() == []


async def test_runtime_unresolved_target_raises(server: _RunningServer) -> None:
    from app.connectors.ssh_channel import SshChannelError

    runtime = ConnectorSessionRuntime(InMemorySessionConnectionResolver())
    with pytest.raises(SshChannelError) as exc_info:
        await runtime.open(_dispatch_request())
    assert exc_info.value.code == "CONNECTOR_TARGET_UNRESOLVED"


async def test_interactive_mode_requires_command_sink(server: _RunningServer) -> None:
    from app.connectors.ssh_channel import SshChannelError

    runtime = ConnectorSessionRuntime(_resolver_for(server, mode=ConnectorSessionMode.INTERACTIVE))
    with pytest.raises(SshChannelError) as exc_info:
        await runtime.open(_dispatch_request())
    assert exc_info.value.code == "CONNECTOR_COMMAND_SINK_MISSING"


# --- 网关集成：create_session → ACTIVE 建立真实连接器会话，close 释放 -------------


@dataclass
class _Grant:
    status: str = "active"
    subject_id: str = "user-1"
    asset_id: str = "asset-1"
    account_id: str = "root"
    protocol: str = "ssh"
    action: str = "session.connect"
    connector_id: str = "conn-1"
    workflow_request_id: str = "wf-1"
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=10))


class _AllowJitGrantClient:
    async def get_grant(self, grant_id: str, *, tenant_id: str) -> _Grant:
        return _Grant()

    async def validate_for_session(self, **_kwargs: object) -> JitGrantSessionBinding:
        return JitGrantSessionBinding(
            jit_grant_id="grant-1",
            workflow_request_id="wf-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )

    async def mark_session_bound(self, **_kwargs: object) -> None:
        return None


class _AllowPolicyClient:
    async def evaluate(self, request: dict) -> dict:
        return {"decision": "allow", "reason_code": "OK", "ttl_seconds": 300, "obligations": []}


async def test_gateway_create_session_opens_and_close_releases_connector(
    server: _RunningServer,
) -> None:
    resolver = _resolver_for(server, mode=ConnectorSessionMode.EXEC)
    runtime = ConnectorSessionRuntime(resolver, id_factory=lambda: "cs-live")
    scheduler = ConnectorRuntimeScheduler(runtime)
    service = SessionGatewayService(
        policy_client=_AllowPolicyClient(),
        token_store=InMemoryConnectionTokenStore(),
        connector_scheduler=scheduler,
        jit_grant_client=_AllowJitGrantClient(),
    )

    issue = await service.issue_connection_token(
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        jit_grant_id="grant-1",
    )
    session = await service.create_session(
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        connection_token=issue.connection_token,
        jit_grant_id="grant-1",
    )

    # 会话进入 ACTIVE，且真的建立了连接器侧会话。
    assert session.status is SessionStatus.ACTIVE
    assert session.connector_session_id == "cs-live"
    assert session.connection_url == "connector-runtime://cs-live"
    assert runtime.active_ids() == ["cs-live"]

    closed = await service.close_session(
        session_id=session.id, subject_id="user-1", reason="done"
    )
    assert closed.status is SessionStatus.CLOSED
    # 网关关闭连带释放连接器侧会话。
    assert runtime.active_ids() == []
