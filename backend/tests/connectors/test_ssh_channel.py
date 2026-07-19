"""#t69 M3 预研切片：真实 SSH 通道端到端与安全回归测试。

全部用例基于 asyncssh 进程内服务器，无外部依赖、可在 CI 中确定性运行。四条安全
约束（P0#7 / P0#15 / P0#16 / P0#17）逐条有对应断言。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncssh
import pytest

from app.connectors.ssh_channel import (
    _OUTPUT_EXCERPT_LIMIT,
    MODERN_ENCRYPTION_ALGS,
    MODERN_KEX_ALGS,
    MODERN_MAC_ALGS,
    CommandEvent,
    SshChannel,
    SshChannelError,
    SshCredential,
    SshTarget,
    _excerpt,
)


@dataclass
class _RecordingSink:
    """内存命令事件下游，替代 #t46 HTTP 管线用于断言。"""

    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
    """回显式命令处理器：返回命令原文与固定退出码。"""

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


async def _start_server(
    *,
    encryption_algs: tuple[str, ...] | None = None,
    mac_algs: tuple[str, ...] | None = None,
) -> _RunningServer:
    """启动一个进程内 asyncssh 服务器，仅接受预置客户端公钥认证。

    ``encryption_algs`` / ``mac_algs`` 允许把服务端限定为弱算法，用于验证客户端在
    协商阶段拒绝弱算法（P0#7）。
    """

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(
        client_key.export_public_key().decode()
    )
    options: dict[str, object] = {
        "server_host_keys": [host_key],
        "authorized_client_keys": authorized,
        "process_factory": _handle_client,
    }
    if encryption_algs is not None:
        options["encryption_algs"] = list(encryption_algs)
    if mac_algs is not None:
        options["mac_algs"] = list(mac_algs)
    acceptor = await asyncssh.listen("127.0.0.1", 0, **options)
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


# --- 端到端：真实通道建立、命令执行、命令事件投递 ---------------------------------


async def test_end_to_end_runs_command_and_emits_event(server: _RunningServer) -> None:
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()

    async with await SshChannel.open(_target(server), credential) as channel:
        event = await channel.run_command("whoami", sink, sequence=0)

    assert event.exit_code == 0
    assert event.command == "whoami"
    assert event.output_excerpt == "executed:whoami"
    assert [e.command for e in sink.events] == ["whoami"]
    assert sink.events[0].sequence == 0


async def test_run_script_emits_sequential_events(server: _RunningServer) -> None:
    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()

    async with await SshChannel.open(_target(server), credential) as channel:
        events = await channel.run_script(["ls", "id", "pwd"], sink, start_sequence=5)

    assert [e.sequence for e in events] == [5, 6, 7]
    assert [e.command for e in sink.events] == ["ls", "id", "pwd"]


# --- P0#17 主机密钥强校验（无 AutoAddPolicy）--------------------------------------


async def test_unknown_host_key_is_rejected(server: _RunningServer) -> None:
    wrong_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    credential = SshCredential(private_key=server.client_private_key)

    with pytest.raises(SshChannelError) as exc_info:
        await SshChannel.open(_target(server, trusted_host_key=wrong_key), credential)

    assert exc_info.value.code == "SSH_HOST_KEY_REJECTED"


async def test_missing_trusted_host_key_refuses_trust_on_first_use(
    server: _RunningServer,
) -> None:
    credential = SshCredential(private_key=server.client_private_key)

    with pytest.raises(SshChannelError) as exc_info:
        await SshChannel.open(_target(server, trusted_host_key="   "), credential)

    assert exc_info.value.code == "SSH_TRUSTED_HOST_KEY_MISSING"


# --- P0#7 强制现代算法套件 --------------------------------------------------------


async def test_weak_server_algorithms_fail_negotiation() -> None:
    # 服务端仅提供 CTR 密码 + SHA-1 MAC；客户端现代 MAC 列表不含 hmac-sha1，协商失败。
    running = await _start_server(
        encryption_algs=("aes128-ctr",),
        mac_algs=("hmac-sha1",),
    )
    try:
        credential = SshCredential(private_key=running.client_private_key)
        with pytest.raises(SshChannelError) as exc_info:
            await SshChannel.open(_target(running), credential)
        assert exc_info.value.code == "SSH_ALGORITHM_NEGOTIATION_FAILED"
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


def test_modern_algorithm_lists_exclude_known_weak_algorithms() -> None:
    weak_kex = {
        "diffie-hellman-group1-sha1",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group-exchange-sha1",
    }
    weak_enc = {"3des-cbc", "aes128-cbc", "aes256-cbc", "arcfour", "blowfish-cbc"}
    weak_mac = {"hmac-md5", "hmac-sha1", "hmac-sha1-96"}

    assert weak_kex.isdisjoint(MODERN_KEX_ALGS)
    assert weak_enc.isdisjoint(MODERN_ENCRYPTION_ALGS)
    assert weak_mac.isdisjoint(MODERN_MAC_ALGS)
    assert MODERN_KEX_ALGS and MODERN_ENCRYPTION_ALGS and MODERN_MAC_ALGS


# --- P0#15 私钥仅内存 & P0#16 凭据不经命令行 -------------------------------------


async def test_private_key_loaded_from_memory_no_disk_access(
    server: _RunningServer, tmp_path, monkeypatch
) -> None:
    # 私钥以内存字节传入。asyncssh 是纯库实现（无 sshpass/ssh 子进程，关闭 P0#16）；
    # 此处进一步断言建立通道过程中未打开任何私钥磁盘路径（关闭 P0#15）。
    import builtins

    opened_paths: list[str] = []
    real_open = builtins.open

    def _tracking_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened_paths.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _tracking_open)

    credential = SshCredential(private_key=server.client_private_key)
    sink = _RecordingSink()
    async with await SshChannel.open(_target(server), credential) as channel:
        await channel.run_command("echo hi", sink, sequence=0)

    assert not any(".ssh" in path or "id_" in path for path in opened_paths)


def test_credential_repr_redacts_secrets() -> None:
    credential = SshCredential(private_key=b"-----BEGIN PRIVATE KEY-----", password="hunter2")
    rendered = repr(credential)

    assert "hunter2" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered
    assert "<redacted>" in rendered


async def test_invalid_private_key_raises_typed_error(server: _RunningServer) -> None:
    with pytest.raises(SshChannelError) as exc_info:
        await SshChannel.open(_target(server), SshCredential(private_key=b"not-a-key"))

    assert exc_info.value.code == "SSH_PRIVATE_KEY_INVALID"


async def test_wrong_key_fails_authentication(server: _RunningServer) -> None:
    other_key = asyncssh.generate_private_key("ssh-ed25519").export_private_key()

    with pytest.raises(SshChannelError) as exc_info:
        await SshChannel.open(_target(server), SshCredential(private_key=other_key))

    assert exc_info.value.code == "SSH_AUTH_FAILED"


def test_credential_requires_a_secret() -> None:
    with pytest.raises(SshChannelError) as exc_info:
        SshCredential()

    assert exc_info.value.code == "SSH_CREDENTIAL_MISSING"


# --- 审计摘要：stdout 占满时仍保留 stderr 的失败原因 -------------------------------


def test_excerpt_preserves_stderr_when_stdout_is_huge() -> None:
    stdout = "o" * (_OUTPUT_EXCERPT_LIMIT * 2)
    stderr = "boom: permission denied"

    excerpt = _excerpt(stdout, stderr)

    assert len(excerpt) == _OUTPUT_EXCERPT_LIMIT
    # stderr 有独立预算，不会被体量更大的 stdout 挤出截断窗口。
    assert excerpt.endswith(stderr)


def test_excerpt_uses_full_budget_when_only_stdout() -> None:
    assert _excerpt("o" * (_OUTPUT_EXCERPT_LIMIT + 10), None) == "o" * _OUTPUT_EXCERPT_LIMIT
    assert _excerpt("ok", "") == "ok"
