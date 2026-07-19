"""命令事件 HTTP sink（#t46 命令事件管线接入 SSH 通道）。

本模块把 :class:`~app.connectors.ssh_channel.CommandEvent` 逐条 POST 到 JanusGate
会话录制入库端点，实现 :class:`~app.connectors.ssh_channel.CommandEventSink` 协议，
从而把连接器侧真实 SSH 通道产生的命令事件接进后端管线。风格对齐
``app/connectors/sdk.py``：构造时可注入 ``httpx.AsyncClient``，非 2xx 响应映射为
不承载任何密钥/token 上下文的类型化异常。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.connectors.ssh_channel import CommandEvent


class CommandEventSinkError(RuntimeError):
    """命令事件入库错误，携带 API 错误元数据但不承载任何密钥/token 上下文。

    :param status_code: 上游返回的 HTTP 状态码。
    :param code: 稳定的机器可读错误码，用于审计与告警。
    :param detail: 面向运维的人类可读描述，不得包含 access token 等敏感信息。
    """

    def __init__(self, *, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"JanusGate command event sink error {status_code}: {code}")


class HttpCommandEventSink:
    """把命令事件 POST 到会话录制入库端点的 HTTP sink。

    满足 :class:`~app.connectors.ssh_channel.CommandEventSink` 协议，可直接作为
    :meth:`SshChannel.run_command` 的 ``sink`` 传入。每条事件投递到::

        POST {base_url}/api/v1/connectors/{connector_id}
             /session-recordings/{recording_id}/commands

    :param base_url: JanusGate 后端基址，尾部斜杠归一化。
    :param access_token: 连接器访问令牌，作为 ``Authorization: Bearer`` 头发送。
    :param connector_id: 事件来源连接器 ID。
    :param recording_id: 目标会话录制 ID。
    :param http_client: 可注入的 ``httpx.AsyncClient``（测试用 MockTransport），
        缺省时自建一个。自建的客户端由本 sink 拥有，:meth:`aclose` 会关闭它；注入的
        客户端生命周期归调用方，:meth:`aclose` 不会关闭。

    推荐以异步上下文管理器使用，确保承载 access token 的自建客户端确定性回收::

        async with HttpCommandEventSink(...) as sink:
            await channel.run_command(cmd, sink, sequence=0)
    """

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        connector_id: int,
        recording_id: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._access_token = access_token
        self._connector_id = connector_id
        self._recording_id = recording_id
        # 记录客户端归属：仅关闭自建客户端，避免误关注入的、由调用方管理的客户端。
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()

    async def emit(self, event: CommandEvent) -> None:
        """把一条命令事件 POST 到入库端点。

        :param event: 待投递的命令事件；``exit_code`` 为 ``None`` 时序列化为 JSON null。
        :raises CommandEventSinkError: 上游返回非 2xx 状态码。
        """

        payload: dict[str, Any] = {
            "sequence": event.sequence,
            "command": event.command,
            "exit_code": event.exit_code,
            "output_excerpt": event.output_excerpt,
        }
        path = (
            f"/api/v1/connectors/{self._connector_id}"
            f"/session-recordings/{self._recording_id}/commands"
        )
        response = await self._client.post(
            self._url(path),
            headers={"Authorization": f"Bearer {self._access_token}"},
            json=payload,
        )
        if response.is_error:
            raise self._error_from_response(response)

    async def aclose(self) -> None:
        """释放自建的底层 HTTP 连接；注入的客户端由调用方负责关闭。"""

        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpCommandEventSink:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path.lstrip("/"))

    @staticmethod
    def _error_from_response(response: httpx.Response) -> CommandEventSinkError:
        body: dict[str, Any]
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            body = {}
        code = str(body.get("code") or body.get("detail") or f"HTTP_{response.status_code}")
        detail = str(body.get("detail") or code)
        return CommandEventSinkError(
            status_code=response.status_code,
            code=code,
            detail=detail,
        )
