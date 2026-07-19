"""#t69：SSH 主机密钥采集（scan → 审批 → 固定）测试。

基于进程内 asyncssh 服务器，自包含、无外部依赖、确定性运行。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncssh
import pytest

from app.connectors.ssh_channel import (
    CommandEvent,
    SshChannel,
    SshChannelError,
    SshCredential,
    SshTarget,
)
from app.connectors.ssh_hostkey import HostKeyScan, scan_host_key


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
    host_key: asyncssh.SSHKey
    client_private_key: bytes


async def _start_server(*, kex_algs: tuple[str, ...] | None = None) -> _RunningServer:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())
    options: dict[str, object] = {
        "server_host_keys": [host_key],
        "authorized_client_keys": authorized,
        "process_factory": _echo_handler,
    }
    if kex_algs is not None:
        options["kex_algs"] = list(kex_algs)
    acceptor = await asyncssh.listen("127.0.0.1", 0, **options)
    return _RunningServer(
        acceptor=acceptor,
        host="127.0.0.1",
        port=acceptor.get_port(),
        host_key=host_key,
        client_private_key=client_key.export_private_key(),
    )


@pytest.fixture
async def server() -> AsyncIterator[_RunningServer]:
    running = await _start_server()
    try:
        yield running
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


async def test_scan_returns_matching_host_key(server: _RunningServer) -> None:
    scan = await scan_host_key(server.host, server.port)

    expected = server.host_key.convert_to_public().export_public_key().decode().strip()
    assert isinstance(scan, HostKeyScan)
    assert scan.key_type == "ssh-ed25519"
    assert scan.public_key == expected
    assert scan.fingerprint == server.host_key.convert_to_public().get_fingerprint()
    assert scan.fingerprint.startswith("SHA256:")


async def test_scanned_key_can_be_pinned_to_open_channel(server: _RunningServer) -> None:
    # 闭环验证：采集到的 public_key 审批后直接作为 trusted_host_key 固定，可建立严格校验连接。
    scan = await scan_host_key(server.host, server.port)
    sink = _RecordingSink()

    target = SshTarget(
        host=server.host,
        port=server.port,
        username="janus",
        trusted_host_key=scan.public_key,
    )
    async with await SshChannel.open(
        target, SshCredential(private_key=server.client_private_key)
    ) as channel:
        event = await channel.run_command("whoami", sink, sequence=0)

    assert event.output_excerpt == "executed:whoami"


async def test_scan_refuses_weak_only_server() -> None:
    running = await _start_server(kex_algs=("diffie-hellman-group14-sha1",))
    try:
        with pytest.raises(SshChannelError) as exc_info:
            await scan_host_key(running.host, running.port, connect_timeout=5)
        assert exc_info.value.code == "SSH_HOST_KEY_SCAN_FAILED"
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


async def test_scan_failure_on_closed_port() -> None:
    # 取一个端口后立即关闭，确保其无监听 → 采集失败并归类。
    probe = await _start_server()
    host, port = probe.host, probe.port
    probe.acceptor.close()
    await probe.acceptor.wait_closed()

    with pytest.raises(SshChannelError) as exc_info:
        await scan_host_key(host, port, connect_timeout=5)

    assert exc_info.value.code == "SSH_HOST_KEY_SCAN_FAILED"
