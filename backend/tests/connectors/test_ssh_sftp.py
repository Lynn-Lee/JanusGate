"""#t69：SFTP 文件传输通道与传输审计测试。

基于进程内 asyncssh SFTP 服务器（chroot 到临时目录），自包含、无外部依赖、确定性运行。
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
import pytest

from app.connectors.ssh_channel import SshChannelError, SshCredential, SshTarget
from app.connectors.ssh_sftp import (
    FileTransferDirection,
    FileTransferEvent,
    FileTransferStatus,
    SftpChannel,
)


@dataclass
class _RecordingSink:
    events: list[FileTransferEvent] = field(default_factory=list)

    async def emit(self, event: FileTransferEvent) -> None:
        self.events.append(event)


@dataclass
class _RunningServer:
    acceptor: asyncssh.SSHAcceptor
    host: str
    port: int
    host_public_key: str
    client_private_key: bytes
    root: Path


@pytest.fixture
async def server(tmp_path: Path) -> AsyncIterator[_RunningServer]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())
    root = tmp_path / "sftproot"
    root.mkdir()

    def sftp_factory(chan: asyncssh.SSHServerChannel) -> asyncssh.SFTPServer:
        return asyncssh.SFTPServer(chan, chroot=str(root).encode())

    acceptor = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        sftp_factory=sftp_factory,
    )
    running = _RunningServer(
        acceptor=acceptor,
        host="127.0.0.1",
        port=acceptor.get_port(),
        host_public_key=host_key.export_public_key().decode().strip(),
        client_private_key=client_key.export_private_key(),
        root=root,
    )
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


def _open(running: _RunningServer, sink: _RecordingSink) -> object:
    return SftpChannel.open(
        _target(running),
        SshCredential(private_key=running.client_private_key),
        sink,
    )


# --- 上传/下载端到端 + 传输审计事件 ----------------------------------------------


async def test_upload_writes_file_and_emits_audit_event(server: _RunningServer) -> None:
    sink = _RecordingSink()
    payload = b"uploaded content"

    async with await _open(server, sink) as sftp:
        event = await sftp.upload_bytes(payload, "/up.txt")

    # 文件确实落到 chroot 根下。
    assert (server.root / "up.txt").read_bytes() == payload
    # 审计事件字段正确，含 SHA-256 与字节数。
    assert event.direction == FileTransferDirection.UPLOAD
    assert event.status == FileTransferStatus.SUCCESS
    assert event.size_bytes == len(payload)
    assert event.sha256 == hashlib.sha256(payload).hexdigest()
    assert sink.events == [event]


async def test_download_reads_file_and_emits_audit_event(server: _RunningServer) -> None:
    sink = _RecordingSink()
    payload = b"downloadable content"
    (server.root / "down.txt").write_bytes(payload)

    async with await _open(server, sink) as sftp:
        data = await sftp.download_bytes("/down.txt")

    assert data == payload
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.direction == FileTransferDirection.DOWNLOAD
    assert event.status == FileTransferStatus.SUCCESS
    assert event.size_bytes == len(payload)
    assert event.sha256 == hashlib.sha256(payload).hexdigest()


async def test_upload_then_download_roundtrip_hash_matches(server: _RunningServer) -> None:
    sink = _RecordingSink()
    payload = b"roundtrip \x00\x01\x02 bytes"

    async with await _open(server, sink) as sftp:
        up = await sftp.upload_bytes(payload, "/rt.bin")
        data = await sftp.download_bytes("/rt.bin")

    assert data == payload
    # 上传与下载的 SHA-256 一致，可用于完整性核对。
    assert up.sha256 == sink.events[1].sha256


# --- 失败路径：失败传输产出 status=failed 审计事件并抛类型化错误 ------------------


async def test_download_missing_file_emits_failed_event(server: _RunningServer) -> None:
    sink = _RecordingSink()

    async with await _open(server, sink) as sftp:
        with pytest.raises(SshChannelError) as exc_info:
            await sftp.download_bytes("/does-not-exist.txt")

    assert exc_info.value.code == "SSH_SFTP_DOWNLOAD_FAILED"
    assert len(sink.events) == 1
    assert sink.events[0].status == FileTransferStatus.FAILED
    assert sink.events[0].direction == FileTransferDirection.DOWNLOAD
    assert sink.events[0].sha256 == ""
    assert sink.events[0].error_code != ""


# --- 安全约束继承：未知主机密钥拒绝 ----------------------------------------------


async def test_open_rejects_untrusted_host_key(server: _RunningServer) -> None:
    wrong_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    sink = _RecordingSink()

    with pytest.raises(SshChannelError) as exc_info:
        await SftpChannel.open(
            _target(server, trusted_host_key=wrong_key),
            SshCredential(private_key=server.client_private_key),
            sink,
        )

    assert exc_info.value.code == "SSH_HOST_KEY_REJECTED"
    assert sink.events == []
