"""文件传输审计 HTTP sink（#t78 文件传输日志接入 SFTP 通道）。

把 :class:`~app.connectors.ssh_sftp.FileTransferEvent` 逐条 POST 到 JanusGate
文件传输入库端点，实现 :class:`~app.connectors.ssh_sftp.FileTransferEventSink`。
风格对齐 :class:`~app.connectors.command_event_sink.HttpCommandEventSink`：可注入
``httpx.AsyncClient``，非 2xx 映射为不承载密钥/token 的类型化异常。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.connectors.ssh_sftp import FileTransferEvent


class FileTransferEventSinkError(RuntimeError):
    """文件传输入库错误，携带 API 错误元数据但不承载任何密钥/token 上下文。

    :param status_code: 上游返回的 HTTP 状态码。
    :param code: 稳定的机器可读错误码，用于审计与告警。
    :param detail: 面向运维的人类可读描述，不得包含 access token 等敏感信息。
    """

    def __init__(self, *, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"JanusGate file transfer event sink error {status_code}: {code}")


class HttpFileTransferEventSink:
    """把文件传输事件 POST 到 #t78 入库端点的 HTTP sink。

    满足 :class:`~app.connectors.ssh_sftp.FileTransferEventSink` 协议。每条事件投递到::

        POST {base_url}/api/v1/connectors/{connector_id}
             /session-recordings/{recording_id}/file-transfers

    :param base_url: JanusGate 后端基址，尾部斜杠归一化。
    :param access_token: 连接器访问令牌，作为 ``Authorization: Bearer`` 头发送。
    :param connector_id: 事件来源连接器 ID。
    :param recording_id: 目标会话录制 ID。
    :param http_client: 可注入的 ``httpx.AsyncClient``（测试用 MockTransport），
        缺省时自建一个。自建的客户端由本 sink 拥有，:meth:`aclose` 会关闭它；注入的
        客户端生命周期归调用方，:meth:`aclose` 不会关闭。
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
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()

    async def emit(self, event: FileTransferEvent) -> None:
        """把一条文件传输事件 POST 到入库端点。

        :param event: 待投递的传输事件；失败时 ``sha256`` 为空串。
        :raises FileTransferEventSinkError: 上游返回非 2xx 状态码。
        """

        payload: dict[str, Any] = {
            "remote_path": event.remote_path,
            "direction": event.direction.value,
            "size_bytes": event.size_bytes,
            "sha256": event.sha256,
            "status": event.status.value,
            "error_code": event.error_code,
        }
        path = (
            f"/api/v1/connectors/{self._connector_id}"
            f"/session-recordings/{self._recording_id}/file-transfers"
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

    async def __aenter__(self) -> HttpFileTransferEventSink:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path.lstrip("/"))

    @staticmethod
    def _error_from_response(response: httpx.Response) -> FileTransferEventSinkError:
        body: dict[str, Any]
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            body = {}
        code = str(body.get("code") or body.get("detail") or f"HTTP_{response.status_code}")
        detail = str(body.get("detail") or code)
        return FileTransferEventSinkError(
            status_code=response.status_code,
            code=code,
            detail=detail,
        )
