"""连接器侧 SSH 会话编排（开通道 → 执行命令 → 投递事件 → 关闭）。

本模块在 :mod:`app.connectors.ssh_channel` 之上收敛出一个薄编排层，把「建立连接、
按序执行一组命令、逐条投递命令事件、无论成败都释放连接」封装成一次可测的调用。
编排层只依赖 ``ssh_channel`` 的公开 API，不承载任何算法/安全策略（那些由底层通道保证）。

失败模式：底层 :class:`~app.connectors.ssh_channel.SshChannelError` 会被归类为
:class:`SshSessionError` 并保留其稳定错误码上抛；无论成功还是失败，连接都会被关闭——
主错误传播时关闭仅作最佳努力（不掩盖主错误），正常路径下关闭失败才归类上抛。
异常不承载任何凭据上下文。
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass, field

import asyncssh

from app.connectors.ssh_channel import (
    CommandEvent,
    CommandEventSink,
    SshChannel,
    SshChannelError,
    SshCredential,
    SshTarget,
)


class SshSessionError(RuntimeError):
    """SSH 会话编排错误，携带稳定错误码，且不承载任何凭据上下文。

    用于把底层 :class:`~app.connectors.ssh_channel.SshChannelError` 归类上抛，
    ``code`` 直接透传底层错误码，便于审计与安全回归断言。

    :param code: 稳定的机器可读错误码（透传自底层通道）。
    :param detail: 面向运维的人类可读描述，不得包含私钥、密码等敏感信息。
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class SshSessionResult:
    """一次 SSH 会话编排的结果汇总。

    :param command_events: 按执行顺序投递的命令事件列表。
    :param exit_codes: 各命令的退出码，无法获取时为 ``None``，与 ``command_events`` 一一对应。
    :param command_count: 实际执行并投递的命令数量。
    :param succeeded: 是否全部命令都以退出码 ``0`` 结束（空命令列表视为成功）。
    """

    command_events: list[CommandEvent] = field(default_factory=list)
    exit_codes: list[int | None] = field(default_factory=list)
    command_count: int = 0
    succeeded: bool = True


class SshConnectorSession:
    """连接器侧 SSH 会话编排器。

    负责把「开通道 → 执行命令 → 投递事件 → 关闭」串成一次调用，本身无状态，可复用。
    """

    async def run(
        self,
        target: SshTarget,
        credential: SshCredential,
        commands: Sequence[str],
        sink: CommandEventSink,
        *,
        start_sequence: int = 0,
    ) -> SshSessionResult:
        """建立连接、按序执行命令并逐条投递事件，最终始终关闭连接。

        :param target: SSH 连接目标与可信主机公钥。
        :param credential: 内存凭据（私钥或密码）。
        :param commands: 待按序执行的命令列表。
        :param sink: 命令事件下游（#t46 管线注入点）。
        :param start_sequence: 首条命令的会话内序号，后续依次递增。
        :returns: 汇总本次会话的 :class:`SshSessionResult`。
        :raises SshSessionError: 底层通道在建立、执行或关闭阶段失败时上抛，``code`` 透传底层
            错误码（关闭失败为 ``SSH_CLOSE_FAILED``）；无论是否抛出，连接都已被关闭。
        """

        channel = await self._open(target, credential)
        try:
            events = await self._run_script(channel, commands, sink, start_sequence)
        except BaseException:
            # 主异常（认证/执行失败等）正在传播时，关闭仅作最佳努力：绝不用次要的
            # teardown 错误掩盖更有意义的主错误。
            with contextlib.suppress(asyncssh.Error, OSError):
                await channel.close()
            raise
        # 正常路径：命令已执行并投递，此时关闭失败应可见，归类为 SshSessionError 上抛。
        await self._close(channel)

        exit_codes = [event.exit_code for event in events]
        return SshSessionResult(
            command_events=events,
            exit_codes=exit_codes,
            command_count=len(events),
            succeeded=all(code == 0 for code in exit_codes),
        )

    @staticmethod
    async def _open(target: SshTarget, credential: SshCredential) -> SshChannel:
        """打开底层通道，把 :class:`SshChannelError` 归类为 :class:`SshSessionError`。"""

        try:
            return await SshChannel.open(target, credential)
        except SshChannelError as exc:
            raise SshSessionError(exc.code, exc.detail) from exc

    @staticmethod
    async def _run_script(
        channel: SshChannel,
        commands: Sequence[str],
        sink: CommandEventSink,
        start_sequence: int,
    ) -> list[CommandEvent]:
        """按序执行命令，把 :class:`SshChannelError` 归类为 :class:`SshSessionError`。"""

        try:
            return await channel.run_script(commands, sink, start_sequence=start_sequence)
        except SshChannelError as exc:
            raise SshSessionError(exc.code, exc.detail) from exc

    @staticmethod
    async def _close(channel: SshChannel) -> None:
        """关闭连接；正常路径下把底层 asyncssh 关闭错误归类为 :class:`SshSessionError` 上抛。

        ``SshChannel.close`` 直接调用 asyncssh，失败时抛出原始 ``asyncssh.Error`` / ``OSError``，
        因此此处按这两类捕获归类，避免未归类异常逃逸。
        """

        try:
            await channel.close()
        except (asyncssh.Error, OSError) as exc:
            raise SshSessionError("SSH_CLOSE_FAILED", str(exc)) from exc
