from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.file_transfer_event_sink import (
    FileTransferEventSinkError,
    HttpFileTransferEventSink,
)
from app.connectors.ssh_sftp import FileTransferDirection, FileTransferEvent, FileTransferStatus


async def test_http_file_transfer_sink_posts_authenticated_event() -> None:
    seen: list[httpx.Request] = []
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        bodies.append(request.content)
        return httpx.Response(201, json={"status": "stored"})

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sink = HttpFileTransferEventSink(
            base_url="https://janusgate.example",
            access_token="connector-token",
            connector_id=7,
            recording_id=42,
            http_client=http_client,
        )
        await sink.emit(
            FileTransferEvent(
                remote_path="/var/tmp/backup.tgz",
                direction=FileTransferDirection.UPLOAD,
                size_bytes=12,
                sha256="a" * 64,
                status=FileTransferStatus.SUCCESS,
            )
        )

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == (
        "/api/v1/connectors/7/session-recordings/42/file-transfers"
    )
    assert request.headers["authorization"] == "Bearer connector-token"
    assert json.loads(bodies[0]) == {
        "remote_path": "/var/tmp/backup.tgz",
        "direction": "upload",
        "size_bytes": 12,
        "sha256": "a" * 64,
        "status": "success",
        "error_code": "",
    }


async def test_http_file_transfer_sink_maps_4xx_without_leaking_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-connector-token"
        return httpx.Response(
            404,
            json={"code": "SESSION_RECORDING_NOT_FOUND", "detail": "SESSION_RECORDING_NOT_FOUND"},
        )

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sink = HttpFileTransferEventSink(
            base_url="https://janusgate.example",
            access_token="secret-connector-token",
            connector_id=7,
            recording_id=42,
            http_client=http_client,
        )
        with pytest.raises(FileTransferEventSinkError) as exc_info:
            await sink.emit(
                FileTransferEvent(
                    remote_path="/etc/hosts",
                    direction=FileTransferDirection.DOWNLOAD,
                    size_bytes=0,
                    sha256="",
                    status=FileTransferStatus.FAILED,
                    error_code="SFTPNoSuchFile",
                )
            )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "SESSION_RECORDING_NOT_FOUND"
    assert "secret-connector-token" not in str(exc_info.value)
    assert "secret-connector-token" not in exc_info.value.detail


async def test_injected_client_is_not_closed_by_file_transfer_sink() -> None:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    sink = HttpFileTransferEventSink(
        base_url="https://janusgate.example",
        access_token="connector-token",
        connector_id=1,
        recording_id=2,
        http_client=http_client,
    )
    await sink.aclose()
    assert http_client.is_closed is False
    await http_client.aclose()


async def test_self_created_file_transfer_client_closed_on_context_exit() -> None:
    async with HttpFileTransferEventSink(
        base_url="https://janusgate.example",
        access_token="connector-token",
        connector_id=1,
        recording_id=2,
    ) as sink:
        assert sink._client.is_closed is False

    assert sink._client.is_closed is True
