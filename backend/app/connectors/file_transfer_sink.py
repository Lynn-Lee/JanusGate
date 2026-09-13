"""文件传输审计 HTTP sink（#t78：把 #t69 SFTP 事件接入入库端点）。

满足 :class:`~app.connectors.ssh_sftp.FileTransferEventSink` 协议，把每次上传/下载
POST 到::

    POST {base_url}/api/v1/connectors/{connector_id}/file-transfers

风格对齐 :class:`~app.connectors.command_event_sink.HttpCommandEventSink`：可注入
``httpx.AsyncClient``；非 2xx 映射为不承载 access token 的类型化异常。
会话身份（session/asset/account）在构造时绑定，因为 :class:`FileTransferEvent`
本身只有路径与摘要、不含网关身份。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.connectors.ssh_sftp import FileTransferEvent


class FileTransferSinkError(RuntimeError):
    """文件传输入库错误，携带 API 错误元数据但不承载任何密钥/token 上下文。"""

    def __init__(self, *, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"JanusGate file transfer sink error {status_code}: {code}")


class HttpFileTransferEventSink:
    """把 SFTP 传输事件 POST 到 #t78 入库端点。"""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        connector_id: int,
        session_id: str,
        asset_id: str,
        account_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._access_token = access_token
        self._connector_id = connector_id
        self._session_id = session_id
        self._asset_id = asset_id
        self._account_id = account_id
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()

    async def emit(self, event: FileTransferEvent) -> None:
        """投递一条传输审计事件。

        :raises FileTransferSinkError: 上游返回非 2xx。
        """

        payload: dict[str, Any] = {
            "session_id": self._session_id,
            "asset_id": self._asset_id,
            "account_id": self._account_id,
            "remote_path": event.remote_path,
            "direction": event.direction.value,
            "size_bytes": event.size_bytes,
            "sha256": event.sha256,
            "status": event.status.value,
            "error_code": event.error_code,
        }
        path = f"/api/v1/connectors/{self._connector_id}/file-transfers"
        response = await self._client.post(
            self._url(path),
            headers={"Authorization": f"Bearer {self._access_token}"},
            json=payload,
        )
        if response.is_error:
            raise self._error_from_response(response)

    async def aclose(self) -> None:
        """释放自建 HTTP 客户端；注入的客户端由调用方关闭。"""

        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpFileTransferEventSink:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path.lstrip("/"))

    @staticmethod
    def _error_from_response(response: httpx.Response) -> FileTransferSinkError:
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            body = {}
        code = str(body.get("code") or body.get("detail") or f"HTTP_{response.status_code}")
        detail = str(body.get("detail") or code)
        return FileTransferSinkError(
            status_code=response.status_code,
            code=code,
            detail=detail,
        )


class AuditServiceFileTransferSink:
    """进程内 SFTP 审计 sink：直接写入 hash chain，无需 HTTP 绕圈。

    用于单机 / 开发装配的 :class:`ConnectorSessionRuntime`；远端连接器应改用
    :class:`HttpFileTransferEventSink`。
    """

    def __init__(
        self,
        *,
        actor: dict[str, Any],
        connector_id: str,
        session_id: str,
        asset_id: str,
        account_id: str,
    ) -> None:
        self._actor = actor
        self._connector_id = connector_id
        self._session_id = session_id
        self._asset_id = asset_id
        self._account_id = account_id

    async def emit(self, event: FileTransferEvent) -> None:
        from app.api.audits.schemas import (
            FileTransferDirection,
            FileTransferIngest,
            FileTransferStatus,
        )
        from app.api.audits.typed import record_file_transfer

        ingest = FileTransferIngest(
            session_id=self._session_id,
            asset_id=self._asset_id,
            account_id=self._account_id,
            remote_path=event.remote_path,
            direction=FileTransferDirection(event.direction.value),
            size_bytes=event.size_bytes,
            sha256=event.sha256,
            status=FileTransferStatus(event.status.value),
            error_code=event.error_code,
        )
        await record_file_transfer(
            ingest=ingest,
            connector_id=self._connector_id,
            actor=self._actor,
        )
