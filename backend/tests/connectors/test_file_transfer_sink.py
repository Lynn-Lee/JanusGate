from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.ssh_sftp import FileTransferDirection, FileTransferEvent, FileTransferStatus


async def test_http_file_transfer_sink_posts_authenticated_event() -> None:
    from app.connectors.file_transfer_sink import HttpFileTransferEventSink

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
            session_id="sess-1",
            asset_id="asset-1",
            account_id="root",
            http_client=http_client,
        )
        await sink.emit(
            FileTransferEvent(
                remote_path="/var/log/app.log",
                direction=FileTransferDirection.DOWNLOAD,
                size_bytes=4,
                sha256="c" * 64,
                status=FileTransferStatus.SUCCESS,
            )
        )

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/connectors/7/file-transfers"
    assert request.headers["authorization"] == "Bearer connector-token"
    assert json.loads(bodies[0]) == {
        "session_id": "sess-1",
        "asset_id": "asset-1",
        "account_id": "root",
        "remote_path": "/var/log/app.log",
        "direction": "download",
        "size_bytes": 4,
        "sha256": "c" * 64,
        "status": "success",
        "error_code": "",
    }


async def test_http_file_transfer_sink_maps_4xx_without_leaking_token() -> None:
    from app.connectors.file_transfer_sink import FileTransferSinkError, HttpFileTransferEventSink

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-connector-token"
        return httpx.Response(403, json={"code": "CONNECTOR_NOT_FOUND", "detail": "CONNECTOR_NOT_FOUND"})

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sink = HttpFileTransferEventSink(
            base_url="https://janusgate.example",
            access_token="secret-connector-token",
            connector_id=7,
            session_id="sess-1",
            asset_id="asset-1",
            account_id="root",
            http_client=http_client,
        )
        with pytest.raises(FileTransferSinkError) as exc_info:
            await sink.emit(
                FileTransferEvent(
                    remote_path="/tmp/x",
                    direction=FileTransferDirection.UPLOAD,
                    size_bytes=1,
                    sha256="d" * 64,
                    status=FileTransferStatus.SUCCESS,
                )
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "CONNECTOR_NOT_FOUND"
    assert "secret-connector-token" not in str(exc_info.value)
    assert "secret-connector-token" not in exc_info.value.detail


async def test_injected_file_transfer_client_is_not_closed() -> None:
    from app.connectors.file_transfer_sink import HttpFileTransferEventSink

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    sink = HttpFileTransferEventSink(
        base_url="https://janusgate.example",
        access_token="connector-token",
        connector_id=1,
        session_id="s",
        asset_id="a",
        account_id="root",
        http_client=http_client,
    )
    await sink.aclose()
    assert http_client.is_closed is False
    await http_client.aclose()


async def test_audit_service_file_transfer_sink_joins_hash_chain(audit_db) -> None:
    from app.api.audits.service import audit_service
    from app.connectors.file_transfer_sink import AuditServiceFileTransferSink

    sink = AuditServiceFileTransferSink(
        actor={
            "id": "user-1",
            "username": "alice",
            "tenant_id": "tenant-a",
        },
        connector_id="7",
        session_id="sess-1",
        asset_id="asset-1",
        account_id="root",
    )
    await sink.emit(
        FileTransferEvent(
            remote_path="/etc/hosts",
            direction=FileTransferDirection.DOWNLOAD,
            size_bytes=8,
            sha256="e" * 64,
            status=FileTransferStatus.SUCCESS,
        )
    )
    items, total = await audit_service.list_events(
        tenant_id="tenant-a",
        event_type=None,
        severity=None,
        limit=50,
        offset=0,
        categories=["file_transfer"],
    )
    assert total == 1
    assert items[0].sequence_number == 1
    assert items[0].metadata["remote_path"] == "/etc/hosts"
    assert items[0].metadata["connector_id"] == "7"
    assert items[0].session_id == "sess-1"
