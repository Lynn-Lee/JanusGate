from __future__ import annotations

import httpx
import pytest

from app.connectors.ssh_channel import CommandEvent


async def test_http_command_event_sink_posts_authenticated_event() -> None:
    from app.connectors.command_event_sink import HttpCommandEventSink

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
        sink = HttpCommandEventSink(
            base_url="https://janusgate.example",
            access_token="connector-token",
            connector_id=7,
            recording_id=42,
            http_client=http_client,
        )
        await sink.emit(
            CommandEvent(
                sequence=3,
                command="whoami",
                exit_code=0,
                output_excerpt="root",
            )
        )

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/connectors/7/session-recordings/42/commands"
    assert request.headers["authorization"] == "Bearer connector-token"

    import json

    body = json.loads(bodies[0])
    assert body == {
        "sequence": 3,
        "command": "whoami",
        "exit_code": 0,
        "output_excerpt": "root",
    }


async def test_http_command_event_sink_serializes_null_exit_code() -> None:
    from app.connectors.command_event_sink import HttpCommandEventSink

    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"status": "stored"})

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sink = HttpCommandEventSink(
            base_url="https://janusgate.example",
            access_token="connector-token",
            connector_id=1,
            recording_id=2,
            http_client=http_client,
        )
        await sink.emit(
            CommandEvent(
                sequence=0,
                command="sleep 1 &",
                exit_code=None,
                output_excerpt="",
            )
        )

    import json

    body = json.loads(bodies[0])
    assert body["exit_code"] is None


async def test_http_command_event_sink_maps_4xx_without_leaking_token() -> None:
    from app.connectors.command_event_sink import (
        CommandEventSinkError,
        HttpCommandEventSink,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-connector-token"
        return httpx.Response(
            403,
            json={
                "code": "RECORDING_NOT_ACTIVE",
                "detail": "RECORDING_NOT_ACTIVE",
            },
        )

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sink = HttpCommandEventSink(
            base_url="https://janusgate.example",
            access_token="secret-connector-token",
            connector_id=7,
            recording_id=42,
            http_client=http_client,
        )
        with pytest.raises(CommandEventSinkError) as exc_info:
            await sink.emit(
                CommandEvent(
                    sequence=1,
                    command="id",
                    exit_code=0,
                    output_excerpt="uid=0",
                )
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "RECORDING_NOT_ACTIVE"
    assert "secret-connector-token" not in str(exc_info.value)
    assert "secret-connector-token" not in exc_info.value.detail


async def test_injected_client_is_not_closed_by_sink() -> None:
    from app.connectors.command_event_sink import HttpCommandEventSink

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    sink = HttpCommandEventSink(
        base_url="https://janusgate.example",
        access_token="connector-token",
        connector_id=1,
        recording_id=2,
        http_client=http_client,
    )

    # 注入的客户端生命周期归调用方，sink.aclose 不得关闭它。
    await sink.aclose()
    assert http_client.is_closed is False
    await http_client.aclose()


async def test_self_created_client_closed_on_context_exit() -> None:
    from app.connectors.command_event_sink import HttpCommandEventSink

    async with HttpCommandEventSink(
        base_url="https://janusgate.example",
        access_token="connector-token",
        connector_id=1,
        recording_id=2,
    ) as sink:
        assert sink._client.is_closed is False

    # 自建客户端由 sink 拥有，上下文退出时确定性回收，避免持 token 的客户端泄漏。
    assert sink._client.is_closed is True
