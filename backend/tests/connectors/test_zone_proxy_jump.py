"""#t67 SSH ProxyJump 端到端测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncssh
import pytest

from app.connectors.ssh_channel import (
    CommandEvent,
    SshChannel,
    SshCredential,
    SshProxyJump,
    SshTarget,
)


@dataclass
class _RecordingSink:
    events: list[CommandEvent]

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
    command = process.command or "<login-shell>"
    process.stdout.write(f"executed:{command}")
    process.exit(0)


class _ForwardingJumpServer(asyncssh.SSHServer):
    """允许 direct-tcpip 转发，供 ProxyJump 测试使用。"""

    def connection_requested(
        self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
    ) -> bool:
        return True


@dataclass
class _RunningServer:
    acceptor: asyncssh.SSHAcceptor
    host: str
    port: int
    host_public_key: str
    client_private_key: bytes


async def _start_server(*, authorized_key: asyncssh.SSHKey | None = None) -> _RunningServer:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(
        (authorized_key or client_key).export_public_key().decode()
    )
    acceptor = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=_handle_client,
        server_factory=lambda: _ForwardingJumpServer(),
    )
    return _RunningServer(
        acceptor=acceptor,
        host="127.0.0.1",
        port=acceptor.get_port(),
        host_public_key=host_key.export_public_key().decode().strip(),
        client_private_key=client_key.export_private_key(),
    )


@pytest.fixture
async def gateway_server() -> AsyncIterator[_RunningServer]:
    running = await _start_server()
    try:
        yield running
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


@pytest.fixture
async def target_server() -> AsyncIterator[_RunningServer]:
    running = await _start_server()
    try:
        yield running
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


async def test_proxy_jump_reaches_target_through_gateway(
    gateway_server: _RunningServer,
    target_server: _RunningServer,
) -> None:
    gateway_cred = SshCredential(private_key=gateway_server.client_private_key)
    target_cred = SshCredential(private_key=target_server.client_private_key)
    proxy = SshProxyJump(
        target=SshTarget(
            host=gateway_server.host,
            port=gateway_server.port,
            username="janus",
            trusted_host_key=gateway_server.host_public_key,
        ),
        credential=gateway_cred,
    )
    target = SshTarget(
        host=target_server.host,
        port=target_server.port,
        username="janus",
        trusted_host_key=target_server.host_public_key,
    )
    sink = _RecordingSink(events=[])

    async with await SshChannel.open(target, target_cred, proxy_jump=proxy) as channel:
        event = await channel.run_command("hostname", sink, sequence=0)

    assert event.exit_code == 0
    assert event.output_excerpt == "executed:hostname"
    assert sink.events[0].command == "hostname"
