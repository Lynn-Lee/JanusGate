"""连接器侧 SSH 会话编排（:mod:`app.connectors.ssh_session`）的端到端与异常路径测试。

全部用例基于 asyncssh 进程内服务器，自包含、无外部依赖，可在 CI 中确定性运行。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import asyncssh
import pytest

from app.connectors.ssh_channel import (
    CommandEvent,
    CommandEventSink,
    SshChannelError,
    SshCredential,
    SshTarget,
)
from app.connectors.ssh_session import SshConnectorSession, SshSessionError


@dataclass
class _RecordingSink:
    """内存命令事件下游，替代 #t46 HTTP 管线用于断言。"""

    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
    """回显式命令处理器：返回命令原文与固定退出码 0。"""

    command = process.command or "<login-shell>"
    process.stdout.write(f"executed:{command}")
    process.exit(0)


@dataclass
class _RunningServer:
    acceptor: asyncssh.SSHAcceptor
    host: str
    port: int
    host_public_key: str
    client_private_key: bytes


async def _start_server() -> _RunningServer:
    """启动一个进程内 asyncssh 服务器，仅接受预置客户端公钥认证。"""

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())
    acceptor = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=_handle_client,
    )
    return _RunningServer(
        acceptor=acceptor,
        host="127.0.0.1",
        port=acceptor.get_port(),
        host_public_key=host_key.export_public_key().decode().strip(),
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


def _target(running: _RunningServer, *, trusted_host_key: str | None = None) -> SshTarget:
    return SshTarget(
        host=running.host,
        port=running.port,
        username="janus",
        trusted_host_key=trusted_host_key or running.host_public_key,
    )


# --- 端到端：开通道 → 执行多条命令 → 按序投递事件 → 关闭 --------------------------


async def test_run_executes_commands_and_emits_ordered_events(server: _RunningServer) -> None:
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()

    result = await SshConnectorSession().run(
        _target(server),
        credential,
        ["ls", "id", "pwd"],
        sink,
        start_sequence=3,
    )

    # 命令事件按序投递到 sink。
    assert [e.command for e in sink.events] == ["ls", "id", "pwd"]
    assert [e.sequence for e in sink.events] == [3, 4, 5]
    assert [e.output_excerpt for e in sink.events] == [
        "executed:ls",
        "executed:id",
        "executed:pwd",
    ]

    # 结果汇总一致且成功。
    assert result.command_events == sink.events
    assert result.exit_codes == [0, 0, 0]
    assert result.command_count == 3
    assert result.succeeded is True


async def test_run_with_no_commands_succeeds_and_emits_nothing(server: _RunningServer) -> None:
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()

    result = await SshConnectorSession().run(_target(server), credential, [], sink)

    assert sink.events == []
    assert result.command_count == 0
    assert result.succeeded is True


# --- 异常路径：主机密钥拒绝时归类为 SshSessionError，连接不建立、私钥不泄露 -------


async def test_untrusted_host_key_raises_session_error(server: _RunningServer) -> None:
    wrong_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()

    with pytest.raises(SshSessionError) as exc_info:
        await SshConnectorSession().run(
            _target(server, trusted_host_key=wrong_key),
            credential,
            ["whoami"],
            sink,
        )

    # 错误码对应底层主机密钥拒绝，且异常文本不泄露私钥。
    assert exc_info.value.code == "SSH_HOST_KEY_REJECTED"
    assert sink.events == []
    private_key_text = server.client_private_key.decode()
    assert private_key_text not in str(exc_info.value)
    assert "PRIVATE KEY" not in str(exc_info.value)


# --- 关闭阶段异常：正常路径归类上抛，主异常在飞时关闭错误不掩盖主错误 -------------
#
# 这两条用例用假通道替换 _open，不建立真实连接：既隔离了关闭语义，也避免把真实
# asyncssh 连接留在打开状态导致 server fixture 的 wait_closed() 挂起。


class _FakeChannel:
    """测试用假通道：run_script 可选抛错，close 始终抛 asyncssh 错误。"""

    def __init__(self, *, run_error: SshChannelError | None = None) -> None:
        self._run_error = run_error
        self.closed = False

    async def run_script(
        self,
        commands: Sequence[str],
        sink: CommandEventSink,
        *,
        start_sequence: int = 0,
    ) -> list[CommandEvent]:
        if self._run_error is not None:
            raise self._run_error
        events = [
            CommandEvent(
                sequence=start_sequence + offset,
                command=command,
                exit_code=0,
                output_excerpt="",
            )
            for offset, command in enumerate(commands)
        ]
        for event in events:
            await sink.emit(event)
        return events

    async def close(self) -> None:
        self.closed = True
        raise asyncssh.Error(code=2, reason="teardown failed")


def _patch_open(monkeypatch: pytest.MonkeyPatch, channel: _FakeChannel) -> None:
    async def _fake_open(target: SshTarget, credential: SshCredential) -> _FakeChannel:
        return channel

    monkeypatch.setattr(SshConnectorSession, "_open", staticmethod(_fake_open))


async def test_close_failure_on_success_path_raises_session_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _FakeChannel()
    _patch_open(monkeypatch, channel)
    sink = _RecordingSink()

    with pytest.raises(SshSessionError) as exc_info:
        await SshConnectorSession().run(
            SshTarget("h", 22, "u", "ssh-ed25519 AAAA"),
            SshCredential(password="pw"),
            ["ls"],
            sink,
        )

    # 命令已执行并投递，但正常路径下的关闭失败被归类为 SSH_CLOSE_FAILED 上抛。
    assert exc_info.value.code == "SSH_CLOSE_FAILED"
    assert [e.command for e in sink.events] == ["ls"]
    assert channel.closed is True


async def test_close_failure_does_not_mask_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _FakeChannel(run_error=SshChannelError("SSH_COMMAND_FAILED", "command channel broke"))
    _patch_open(monkeypatch, channel)
    sink = _RecordingSink()

    with pytest.raises(SshSessionError) as exc_info:
        await SshConnectorSession().run(
            SshTarget("h", 22, "u", "ssh-ed25519 AAAA"),
            SshCredential(password="pw"),
            ["ls"],
            sink,
        )

    # 主错误（执行失败）优先上抛，关闭失败不得将其覆盖为 SSH_CLOSE_FAILED。
    assert exc_info.value.code == "SSH_COMMAND_FAILED"
    assert channel.closed is True
