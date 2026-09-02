"""#t68 K8s TokenRequest API 客户端测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.k8s.token_request import K8sTokenRequestError, request_service_account_token

_FAKE_CA = """-----BEGIN CERTIFICATE-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA1234567890abcdef
-----END CERTIFICATE-----"""


@pytest.mark.asyncio
async def test_request_service_account_token_returns_bearer_token() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"status": {"token": "short-lived-token"}}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.k8s.token_request._ssl_context_from_ca", return_value=True),
        patch("app.k8s.token_request.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await request_service_account_token(
            api_server="https://k8s.example:6443",
            server_ca_pem=_FAKE_CA,
            bootstrap_token="bootstrap-token",
            namespace="default",
            service_account="default",
            expiration_seconds=3600,
        )

    assert result.token == "short-lived-token"
    assert result.expiration_seconds == 3600
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.await_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "bootstrap-token" not in url
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer bootstrap-token"


@pytest.mark.asyncio
async def test_request_service_account_token_maps_api_error() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.json.return_value = {}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.k8s.token_request._ssl_context_from_ca", return_value=True),
        patch("app.k8s.token_request.httpx.AsyncClient", return_value=mock_client),
    ):
        with pytest.raises(K8sTokenRequestError, match="K8S_TOKEN_REQUEST_REJECTED"):
            await request_service_account_token(
                api_server="https://k8s.example:6443",
                server_ca_pem=_FAKE_CA,
                bootstrap_token="bootstrap-token",
                namespace="default",
                service_account="default",
            )
