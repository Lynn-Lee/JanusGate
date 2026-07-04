"""Lightweight HTTP SDK for connector management clients."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from app.api.connector_schemas import ConnectorResponse
from app.connectors.schemas import ConnectorCapability


class ConnectorSdkError(RuntimeError):
    """SDK error that carries API error metadata without secret-bearing context."""

    def __init__(self, *, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"JanusGate connector API error {status_code}: {code}")


class ConnectorSdkClient:
    """Small async client for JanusGate connector lifecycle calls."""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._access_token = access_token
        self._client = http_client or httpx.AsyncClient()

    async def create_connector(
        self,
        *,
        name: str,
        environment: str,
        public_key_fingerprint: str,
        capabilities: list[ConnectorCapability],
        mtls_certificate_fingerprint: str | None = None,
        attestation_nonce: str | None = None,
        attestation_digest: str | None = None,
    ) -> ConnectorResponse:
        payload: dict[str, Any] = {
            "name": name,
            "environment": environment,
            "public_key_fingerprint": public_key_fingerprint,
            "capabilities": [capability.value for capability in capabilities],
        }
        if mtls_certificate_fingerprint is not None:
            payload["mtls_certificate_fingerprint"] = mtls_certificate_fingerprint
        if attestation_nonce is not None:
            payload["attestation_nonce"] = attestation_nonce
        if attestation_digest is not None:
            payload["attestation_digest"] = attestation_digest

        return await self._request("POST", "/api/v1/connectors/", json=payload)

    async def heartbeat(self, connector_id: int) -> ConnectorResponse:
        return await self._request("POST", f"/api/v1/connectors/{connector_id}/heartbeat")

    async def rotate_key(
        self,
        connector_id: int,
        *,
        public_key_fingerprint: str,
    ) -> ConnectorResponse:
        return await self._request(
            "POST",
            f"/api/v1/connectors/{connector_id}/rotate-key",
            json={"public_key_fingerprint": public_key_fingerprint},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> ConnectorResponse:
        response = await self._client.request(
            method,
            self._url(path),
            headers={"Authorization": f"Bearer {self._access_token}"},
            json=json,
        )
        if response.is_error:
            raise self._error_from_response(response)
        return ConnectorResponse.model_validate(response.json())

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path.lstrip("/"))

    @staticmethod
    def _error_from_response(response: httpx.Response) -> ConnectorSdkError:
        body: dict[str, Any]
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            body = {}
        code = str(body.get("code") or body.get("detail") or f"HTTP_{response.status_code}")
        detail = str(body.get("detail") or code)
        return ConnectorSdkError(
            status_code=response.status_code,
            code=code,
            detail=detail,
        )
