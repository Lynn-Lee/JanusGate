"""SSH 交互式 PTY 通道与命令流解析（#t69：PTY 交互 + 命令流解析接入 #t46）。

本模块在 :mod:`app.connectors.ssh_channel` 的安全连接之上提供交互式 shell 能力，
并把交互式会话中操作员执行的命令逐条解析、投递到命令事件管线。包含两部分：

- :class:`InteractiveCommandParser`：纯粹、无 I/O 的命令行重建器，从操作员的**键盘输入
  流**（而非终端回显）中还原出实际敲下的命令。堡垒机审计关注的是「用户到底执行了什么」，
  因此以输入流为准，并正确处理退格、Ctrl-C/Ctrl-U 取消与 ANSI 转义序列。
- :class:`SshInteractiveSession`：在安全连接上开 PTY shell，采用 **prompt 跟踪**做输出
  归属（真实堡垒机的常用技法，也让审计与测试确定化），每条命令投递一条
  :class:`~app.connectors.ssh_channel.CommandEvent`。

安全约束继承自 :meth:`SshChannel.open`（P0#7/15/16/17），本模块不放宽任何一项。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import asyncssh

from app.connectors.command_policy import CommandPolicyGuard, default_command_policy_guard
from app.connectors.ssh_channel import (
    _OUTPUT_EXCERPT_LIMIT,
    CommandEvent,
    CommandEventSink,
    SshChannel,
    SshChannelError,
    SshCredential,
    SshTarget,
)
from app.policy.schemas import ResourceRef, SubjectRef

# 控制字符
_ESC = "\x1b"
_BACKSPACE = {"\x7f", "\x08"}
_CTRL_C = "\x03"
_CTRL_U = "\x15"

# 默认 prompt 哨兵：真实部署应把目标 shell 的 PS1 设为一个唯一标记，以便精确切分
# 每条命令的输出。测试用进程内 shell 会写出该标记。
DEFAULT_PROMPT = "jgt$ "


class InteractiveCommandParser:
    """从操作员键盘输入流重建命令行的纯解析器。

    逐字符消费输入，遇回车/换行时产出一条完整命令。正确处理：可打印字符累积、
    退格删除、Ctrl-U 清行、Ctrl-C 取消当前行（该行不作为命令产出）、以及 ANSI
    CSI 转义序列（方向键等）的丢弃。解析以**输入**为准，不依赖终端回显。

    已知限制：不解释 Tab 补全（Tab 被忽略），不跟踪光标中间插入/移动，
    这些留待后续完整 #t69 细化。
    """

    def __init__(self) -> None:
        self._line: list[str] = []
        self._swallow_lf = False
        self._in_escape = False
        self._in_csi = False

    def feed(self, data: str) -> list[str]:
        """消费一段输入，返回本次产出的完整命令列表（可能为空）。

        :param data: 一段操作员键盘输入（可跨命令、可为半条命令）。
        :returns: 本次输入中被回车/换行终止且非空白的命令，按顺序排列。
        """

        commands: list[str] = []
        for char in data:
            command = self._consume(char)
            if command is not None:
                commands.append(command)
        return commands

    def _consume(self, char: str) -> str | None:
        """消费单个字符；若该字符终止了一条非空命令则返回它，否则返回 ``None``。"""

        if self._in_escape:
            self._consume_escape(char)
            return None
        if char == _ESC:
            self._in_escape = True
            self._in_csi = False
            return None
        if char in ("\r", "\n"):
            return self._terminate(char)
        # 非终止字符出现时，清除可能残留的 \r\n 吞并状态。
        self._swallow_lf = False
        if char in _BACKSPACE:
            if self._line:
                self._line.pop()
            return None
        if char == _CTRL_U:
            self._line.clear()
            return None
        if char == _CTRL_C:
            # 取消当前行：该行不会被 shell 执行，故不作为命令产出。
            self._line.clear()
            return None
        if char >= " " and char != "\x7f":
            self._line.append(char)
        # 其它控制字符（Tab、Ctrl-D 等）在解析层忽略。
        return None

    def _consume_escape(self, char: str) -> None:
        """在转义序列状态内消费字符，直到序列结束。"""

        if not self._in_csi:
            # ESC 之后：'[' 进入 CSI，否则视为两字符转义，直接结束。
            if char == "[":
                self._in_csi = True
            else:
                self._in_escape = False
            return
        # CSI 参数字节持续到一个 0x40-0x7e 的最终字节为止。
        if "\x40" <= char <= "\x7e":
            self._in_escape = False
            self._in_csi = False

    def _terminate(self, char: str) -> str | None:
        """处理行终止符，返回被终止的非空命令或 ``None``。"""

        if char == "\n" and self._swallow_lf:
            # 紧跟在 \r 之后的 \n：属于同一个 \r\n 终止符，跳过。
            self._swallow_lf = False
            return None
        self._swallow_lf = char == "\r"
        command = "".join(self._line).strip()
        self._line.clear()
        return command or None


class SshInteractiveSession:
    """交互式 PTY 会话：在安全连接上开 shell 并把每条命令投递到命令事件管线。

    采用 prompt 跟踪切分命令输出：:meth:`open` 消费到首个 prompt 后就绪，
    :meth:`run_command` 发送一条命令并读取到下一个 prompt，作为该命令的输出。
    """

    def __init__(
        self,
        channel: SshChannel,
        process: asyncssh.SSHClientProcess[str],
        sink: CommandEventSink,
        parser: InteractiveCommandParser,
        *,
        prompt: str,
        start_sequence: int,
        read_chunk: int,
        read_timeout: float,
        policy: CommandPolicyGuard,
    ) -> None:
        self._channel = channel
        self._process = process
        self._sink = sink
        self._parser = parser
        self._prompt = prompt
        self._sequence = start_sequence
        self._read_chunk = read_chunk
        self._read_timeout = read_timeout
        self._policy = policy

    @classmethod
    async def open(
        cls,
        target: SshTarget,
        credential: SshCredential,
        sink: CommandEventSink,
        *,
        prompt: str = DEFAULT_PROMPT,
        term_type: str = "xterm",
        start_sequence: int = 0,
        connect_timeout: float = 10.0,
        read_chunk: int = 1024,
        read_timeout: float = 30.0,
        policy: CommandPolicyGuard | None = None,
        subject: SubjectRef | None = None,
        resource: ResourceRef | None = None,
        account_id: str = "",
        session_id: str | None = None,
        session_factory: Any = None,
        db: Any = None,
    ) -> SshInteractiveSession:
        """建立安全连接、打开 PTY shell，并消费到首个 prompt 后返回就绪会话。

        :param read_timeout: 单次读取 prompt 的超时秒数；目标长时间不产出 prompt 时避免永久挂起。
        :raises SshChannelError: 建立连接或打开交互进程失败；失败时不遗留连接。
        """

        resolved = policy or await default_command_policy_guard(
            subject=subject,
            resource=resource,
            account_id=account_id,
            session_id=session_id,
            session_factory=session_factory,
            db=db,
        )
        channel = await SshChannel.open(
            target, credential, connect_timeout=connect_timeout, policy=resolved
        )
        try:
            process = await channel.start_interactive(term_type=term_type)
        except BaseException:
            with contextlib.suppress(asyncssh.Error, OSError):
                await channel.close()
            raise
        session = cls(
            channel,
            process,
            sink,
            InteractiveCommandParser(),
            prompt=prompt,
            start_sequence=start_sequence,
            read_chunk=read_chunk,
            read_timeout=read_timeout,
            policy=resolved,
        )
        try:
            await session._read_until_prompt()  # 消费初始 banner 与首个 prompt
        except BaseException:
            await session.close()
            raise
        return session

    async def run_command(self, command_line: str) -> CommandEvent:
        """在交互 shell 中执行一条命令，投递命令事件并返回它。

        :param command_line: 单行命令（不含换行终止符）。
        :returns: 已投递的命令事件；``exit_code`` 为 ``None``（交互 shell 不逐条回传退出码）。
        :raises SshChannelError: 交互通道读写失败（``SSH_INTERACTIVE_IO_FAILED``）。
        """

        parsed = self._parser.feed(command_line + "\r")
        canonical = parsed[-1] if parsed else command_line.strip()
        decision = await self._policy.authorize(canonical)
        if not decision.allowed:
            raise SshChannelError(
                "SSH_COMMAND_DENIED",
                decision.reason_code,
                audit_event_id=decision.audit_event_id,
            )
        try:
            self._process.stdin.write(command_line + "\n")
            raw = await self._read_until_prompt()
        except (asyncssh.Error, OSError) as exc:
            raise SshChannelError("SSH_INTERACTIVE_IO_FAILED", str(exc)) from exc
        output = self._clean_output(raw, canonical)
        output = self._policy.mask_text(output)
        event = CommandEvent(
            sequence=self._sequence,
            command=canonical,
            exit_code=None,
            output_excerpt=output[:_OUTPUT_EXCERPT_LIMIT],
        )
        self._sequence += 1
        await self._sink.emit(event)
        return event

    async def close(self) -> None:
        """关闭交互进程与底层连接；关闭属最佳努力，不掩盖调用方在飞的异常。"""

        with contextlib.suppress(asyncssh.Error, OSError):
            self._process.close()
        await self._channel.close()

    async def __aenter__(self) -> SshInteractiveSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def _read_until_prompt(self) -> str:
        """读取输出直到出现 prompt 标记；返回 prompt 之前的内容。

        :raises SshChannelError: 单次读取超过 ``read_timeout`` 仍未见 prompt
            （``SSH_INTERACTIVE_READ_TIMEOUT``），避免目标不产出 prompt 时永久挂起。
        """

        buf = ""
        while self._prompt not in buf:
            try:
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(self._read_chunk),
                    timeout=self._read_timeout,
                )
            except TimeoutError as exc:
                raise SshChannelError(
                    "SSH_INTERACTIVE_READ_TIMEOUT",
                    "timed out waiting for shell prompt",
                ) from exc
            if not chunk:  # EOF：进程已退出，返回已读到的内容。
                return buf
            buf += chunk
        return buf[: buf.rfind(self._prompt)]

    def _clean_output(self, raw: str, command: str) -> str:
        """剥离 PTY 回显的命令行本身，返回规整后的命令输出。"""

        text = raw
        for prefix in (f"{command}\r\n", f"{command}\n", f"{command}\r"):
            if text.startswith(prefix):
                text = text[len(prefix) :]
                break
        return text.strip("\r\n")
