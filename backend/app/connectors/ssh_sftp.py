"""SSH 上的 SFTP 文件传输通道与传输审计（#t69：SFTP 接入 #t78 文件传输审计）。

在 :mod:`app.connectors.ssh_channel` 的安全连接之上提供 SFTP 传输，并把每次上传/下载
产生一条 :class:`FileTransferEvent` 传输审计事件投递到下游 sink。安全约束继承自
:meth:`SshChannel.open`（P0#7/15/16/17），本模块不放宽任何一项。

事件携带 SHA-256 摘要与字节数，便于并入 #t61 的 hash chain 与 WORM 归档（#t78 约束）；
失败的传输同样产出一条 ``status=failed`` 的事件，保证失败传输在审计中可见。

说明：#t78 的文件传输日志入库端点尚未建立（属 M6），本切片先以
:class:`FileTransferEventSink` 协议解耦下游；待 #t78 落地入库端点后，提供 HTTP sink 即可
接线，无需改动本传输通道（与命令事件 sink 的做法一致）。
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import asyncssh

from app.connectors.ssh_channel import (
    SshChannel,
    SshChannelError,
    SshCredential,
    SshTarget,
)


class FileTransferDirection(StrEnum):
    """文件传输方向。"""

    UPLOAD = "upload"
    DOWNLOAD = "download"


class FileTransferStatus(StrEnum):
    """文件传输结果状态。"""

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class FileTransferEvent:
    """一次 SFTP 文件传输的审计事件。

    :param remote_path: 远端文件路径。
    :param direction: 传输方向（上传/下载）。
    :param size_bytes: 传输字节数；失败时为已知的部分大小或 ``0``。
    :param sha256: 传输内容的 SHA-256 十六进制摘要；失败时为空串。
    :param status: 传输结果状态。
    :param error_code: 失败时的错误分类（异常类型名）；成功时为空串。
    """

    remote_path: str
    direction: FileTransferDirection
    size_bytes: int
    sha256: str
    status: FileTransferStatus
    error_code: str = ""


class FileTransferEventSink(Protocol):
    """文件传输审计事件下游（#t78 文件传输日志的注入点）。"""

    async def emit(self, event: FileTransferEvent) -> None:
        """接收一条文件传输审计事件。"""
        ...


class SftpChannel:
    """SFTP 文件传输通道，逐次传输产出传输审计事件。

    通过 :meth:`open` 在安全连接上建立 SFTP 会话，:meth:`upload_bytes` /
    :meth:`download_bytes` 执行传输并向 sink 投递审计事件，:meth:`close` 释放会话。
    支持异步上下文管理器语义。
    """

    def __init__(
        self,
        channel: SshChannel,
        sftp: asyncssh.SFTPClient,
        sink: FileTransferEventSink,
    ) -> None:
        self._channel = channel
        self._sftp = sftp
        self._sink = sink

    @classmethod
    async def open(
        cls,
        target: SshTarget,
        credential: SshCredential,
        sink: FileTransferEventSink,
        *,
        connect_timeout: float = 10.0,
    ) -> SftpChannel:
        """建立安全连接并打开 SFTP 会话。

        :raises SshChannelError: 建立连接或打开 SFTP 会话失败；失败时不遗留连接。
        """

        channel = await SshChannel.open(target, credential, connect_timeout=connect_timeout)
        try:
            sftp = await channel.start_sftp()
        except BaseException:
            with contextlib.suppress(asyncssh.Error, OSError):
                await channel.close()
            raise
        return cls(channel, sftp, sink)

    async def upload_bytes(self, data: bytes, remote_path: str) -> FileTransferEvent:
        """上传内存字节到远端路径，投递并返回传输审计事件。

        :raises SshChannelError: 传输失败（``SSH_SFTP_UPLOAD_FAILED``）；失败前已投递一条
            ``status=failed`` 审计事件。
        """

        try:
            async with self._sftp.open(remote_path, "wb") as handle:
                await handle.write(data)
        except (asyncssh.Error, OSError) as exc:
            await self._emit_failed(remote_path, FileTransferDirection.UPLOAD, len(data), exc)
            raise SshChannelError("SSH_SFTP_UPLOAD_FAILED", str(exc)) from exc
        return await self._emit_success(remote_path, FileTransferDirection.UPLOAD, data)

    async def download_bytes(self, remote_path: str) -> bytes:
        """下载远端路径内容为内存字节，投递传输审计事件并返回内容。

        :raises SshChannelError: 传输失败（``SSH_SFTP_DOWNLOAD_FAILED``）；失败前已投递一条
            ``status=failed`` 审计事件。
        """

        try:
            async with self._sftp.open(remote_path, "rb") as handle:
                data = await handle.read()
        except (asyncssh.Error, OSError) as exc:
            await self._emit_failed(remote_path, FileTransferDirection.DOWNLOAD, 0, exc)
            raise SshChannelError("SSH_SFTP_DOWNLOAD_FAILED", str(exc)) from exc
        payload = data if isinstance(data, bytes) else data.encode("utf-8")
        await self._emit_success(remote_path, FileTransferDirection.DOWNLOAD, payload)
        return payload

    async def close(self) -> None:
        """关闭 SFTP 会话与底层连接；SFTP 退出属最佳努力，不掩盖在飞的异常。"""

        with contextlib.suppress(asyncssh.Error, OSError):
            self._sftp.exit()
        await self._channel.close()

    async def __aenter__(self) -> SftpChannel:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def _emit_success(
        self,
        remote_path: str,
        direction: FileTransferDirection,
        data: bytes,
    ) -> FileTransferEvent:
        event = FileTransferEvent(
            remote_path=remote_path,
            direction=direction,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            status=FileTransferStatus.SUCCESS,
        )
        await self._sink.emit(event)
        return event

    async def _emit_failed(
        self,
        remote_path: str,
        direction: FileTransferDirection,
        size_bytes: int,
        exc: Exception,
    ) -> None:
        event = FileTransferEvent(
            remote_path=remote_path,
            direction=direction,
            size_bytes=size_bytes,
            sha256="",
            status=FileTransferStatus.FAILED,
            error_code=type(exc).__name__,
        )
        await self._sink.emit(event)
