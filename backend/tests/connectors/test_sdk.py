from __future__ import annotations

import httpx
import pytest

from app.connectors.schemas import ConnectorCapability


@pytest.mark.asyncio
async def test_connector_sdk_sends_authenticated_create_heartbeat_and_rotate_key_requests() -> None:
    from app.connectors.sdk import ConnectorSdkClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer admin-token"
        if request.url.path == "/api/v1/connectors/" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": 7,
                    "tenant_id": "tenant-a",
                    "name": "koko-prod-1",
                    "environment": "prod",
                    "public_key_fingerprint": "sha256:key-old",
                    "previous_public_key_fingerprint": None,
                    "capabilities": ["ssh"],
                    "status": "active",
                    "mtls_bound": True,
                    "attestation_bound": False,
                    "registered_at": "2026-07-04T00:00:00Z",
                    "last_heartbeat_at": None,
                    "key_rotated_at": None,
                },
            )
        if request.url.path == "/api/v1/connectors/7/heartbeat" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "tenant_id": "tenant-a",
                    "name": "koko-prod-1",
                    "environment": "prod",
                    "public_key_fingerprint": "sha256:key-old",
                    "previous_public_key_fingerprint": None,
                    "capabilities": ["ssh"],
                    "status": "active",
                    "mtls_bound": True,
                    "attestation_bound": False,
                    "registered_at": "2026-07-04T00:00:00Z",
                    "last_heartbeat_at": "2026-07-04T00:01:00Z",
                    "key_rotated_at": None,
                },
            )
        if request.url.path == "/api/v1/connectors/7/rotate-key" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "tenant_id": "tenant-a",
                    "name": "koko-prod-1",
                    "environment": "prod",
                    "public_key_fingerprint": "sha256:key-new",
                    "previous_public_key_fingerprint": "sha256:key-old",
                    "capabilities": ["ssh"],
                    "status": "active",
                    "mtls_bound": True,
                    "attestation_bound": False,
                    "registered_at": "2026-07-04T00:00:00Z",
                    "last_heartbeat_at": "2026-07-04T00:01:00Z",
                    "key_rotated_at": "2026-07-04T00:02:00Z",
                },
            )
        return httpx.Response(404, json={"code": "NOT_FOUND", "detail": "NOT_FOUND"})

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sdk = ConnectorSdkClient(
            base_url="https://janusgate.example",
            access_token="admin-token",
            http_client=http_client,
        )

        created = await sdk.create_connector(
            name="koko-prod-1",
            environment="prod",
            public_key_fingerprint="sha256:key-old",
            capabilities=[ConnectorCapability.SSH],
            mtls_certificate_fingerprint="sha256:client-cert",
        )
        heartbeat = await sdk.heartbeat(created.id)
        rotated = await sdk.rotate_key(created.id, public_key_fingerprint="sha256:key-new")

    assert [request.url.path for request in seen] == [
        "/api/v1/connectors/",
        "/api/v1/connectors/7/heartbeat",
        "/api/v1/connectors/7/rotate-key",
    ]
    assert created.id == 7
    assert heartbeat.last_heartbeat_at is not None
    assert rotated.public_key_fingerprint == "sha256:key-new"
    assert rotated.previous_public_key_fingerprint == "sha256:key-old"


@pytest.mark.asyncio
async def test_connector_sdk_error_does_not_leak_access_token() -> None:
    from app.connectors.sdk import ConnectorSdkClient, ConnectorSdkError

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-admin-token"
        return httpx.Response(
            403,
            json={
                "code": "CONNECTOR_NOT_ACTIVE",
                "detail": "CONNECTOR_NOT_ACTIVE",
                "message": "CONNECTOR_NOT_ACTIVE",
            },
        )

    async with httpx.AsyncClient(
        base_url="https://janusgate.example",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sdk = ConnectorSdkClient(
            base_url="https://janusgate.example",
            access_token="secret-admin-token",
            http_client=http_client,
        )

        with pytest.raises(ConnectorSdkError) as exc_info:
            await sdk.heartbeat(7)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "CONNECTOR_NOT_ACTIVE"
    assert "secret-admin-token" not in str(exc_info.value)
