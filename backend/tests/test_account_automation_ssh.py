"""#t73 SSH 改密 / 校验端到端：密码只走 stdin，不经命令行（关闭 P0#16）。"""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncssh
import pytest

from app.services import account_automation as automation_mod
from app.services.account_automation import (
    SshAccountAutomationExecutor,
    SshAutomationTarget,
    _chpasswd,
)


@dataclass
class _Captured:
    commands: list[str] = field(default_factory=list)
    stdin_chunks: list[str] = field(default_factory=list)


_CAPTURE = _Captured()


async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
    command = process.command or ""
    _CAPTURE.commands.append(command)
    stdin = ""
    if process.stdin is not None:
        try:
            data = await asyncio.wait_for(process.stdin.read(), timeout=2)
        except TimeoutError:
            data = b""
        stdin = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        _CAPTURE.stdin_chunks.append(stdin)
    if command == "chpasswd":
        process.exit(0 if ":" in stdin else 1)
        return
    if command in {"true", "getent passwd"}:
        if command == "getent passwd":
            process.stdout.write("root:x:0:0:root:/root:/bin/bash\ndeploy:x:1000:1000::/home/deploy:/bin/bash\n")
        process.exit(0)
        return
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
    _CAPTURE.commands.clear()
    _CAPTURE.stdin_chunks.clear()
    running = await _start_server()
    try:
        yield running
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


class _KeySecretStore:
    def __init__(self, private_key: bytes) -> None:
        self._private_key = private_key.decode() if isinstance(private_key, bytes) else private_key

    async def unwrap(self, secret_id: str) -> str:
        del secret_id
        return self._private_key

    async def rotate(self, secret_id: str, new_plaintext: str) -> object:
        del secret_id, new_plaintext
        return object()

    async def create_secret(self, name: str, plaintext: str) -> object:
        del name, plaintext
        return object()


def _target(server: _RunningServer) -> SshAutomationTarget:
    return SshAutomationTarget(
        account_id=1,
        asset_id=1,
        tenant_id="tenant-a",
        username="janus",
        protocol="ssh",
        address=server.host,
        port=server.port,
        secret_id="sec_janus",
        trusted_host_key=server.host_public_key,
    )


def test_chpasswd_never_embeds_password_in_command_string() -> None:
    source = inspect.getsource(_chpasswd)
    assert 'conn.run("chpasswd"' in source
    assert "input=" in source
    tree = ast.parse(inspect.getsource(automation_mod))
    assert not any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"
        for node in ast.walk(tree)
    )
    # 模块 docstring 会提及 sshpass 作为「禁止项」；断言真实代码路径不调用它。
    assert "sshpass" not in source
    assert not any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and "sshpass" in node.value
        for node in ast.walk(ast.parse(source))
    )


@pytest.mark.asyncio
async def test_change_secret_sends_password_on_stdin_not_argv(server: _RunningServer) -> None:
    executor = SshAccountAutomationExecutor(secrets_store=_KeySecretStore(server.client_private_key))
    new_password = "N3w!Rotation-Pass"
    result = await executor.change_secret(_target(server), new_password=new_password)
    assert result.summary == "secret_changed"
    assert _CAPTURE.commands == ["chpasswd"]
    assert all(new_password not in command for command in _CAPTURE.commands)
    assert any(new_password in chunk for chunk in _CAPTURE.stdin_chunks)
    assert any(chunk.startswith("janus:") for chunk in _CAPTURE.stdin_chunks)


@pytest.mark.asyncio
async def test_verify_account_succeeds_without_subprocess(server: _RunningServer) -> None:
    executor = SshAccountAutomationExecutor(secrets_store=_KeySecretStore(server.client_private_key))
    result = await executor.verify_account(_target(server))
    assert result.summary == "account_verified"
    assert "true" in _CAPTURE.commands


@pytest.mark.asyncio
async def test_gather_accounts_parses_passwd(server: _RunningServer) -> None:
    executor = SshAccountAutomationExecutor(secrets_store=_KeySecretStore(server.client_private_key))
    result, discovered = await executor.gather_accounts(_target(server))
    assert "gathered accounts=" in result.summary
    names = {item.username for item in discovered}
    assert {"root", "deploy"} <= names
    assert any(risk.risk_type == "privileged" for risk in result.risks)
