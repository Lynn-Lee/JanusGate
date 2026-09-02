"""#t68 Kubernetes TokenRequest API 客户端（短期 token 签发）。"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class TokenRequestResult:
    token: str
    expiration_seconds: int


class K8sTokenRequestError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _ssl_context_from_ca(server_ca_pem: str) -> ssl.SSLContext:
    context = ssl.create_default_context(cadata=server_ca_pem)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def request_service_account_token(
    *,
    api_server: str,
    server_ca_pem: str,
    bootstrap_token: str,
    namespace: str,
    service_account: str,
    expiration_seconds: int = 3600,
    audiences: tuple[str, ...] = ("https://kubernetes.default.svc",),
) -> TokenRequestResult:
    """调用 K8s TokenRequest 子资源签发短期 Bearer token。

    bootstrap_token 为 Vault 中经审批解包后的长期 ServiceAccount token，仅用于
    本次 HTTP 请求内存传递，不经命令行或 URL query（关闭 P0#16）。
    """

    base = api_server.rstrip("/")
    url = (
        f"{base}/api/v1/namespaces/{namespace}/serviceaccounts/"
        f"{service_account}/token"
    )
    payload: dict[str, Any] = {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenRequest",
        "spec": {
            "audiences": list(audiences),
            "expirationSeconds": expiration_seconds,
        },
    }
    headers = {"Authorization": f"Bearer {bootstrap_token}"}
    try:
        async with httpx.AsyncClient(
            verify=_ssl_context_from_ca(server_ca_pem),
            timeout=10.0,
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise K8sTokenRequestError("K8S_TOKEN_REQUEST_TIMEOUT", "token request timed out") from exc
    except httpx.RequestError as exc:
        raise K8sTokenRequestError("K8S_TOKEN_REQUEST_FAILED", str(exc)) from exc

    if response.status_code >= 400:
        raise K8sTokenRequestError(
            "K8S_TOKEN_REQUEST_REJECTED",
            f"api server returned status {response.status_code}",
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise K8sTokenRequestError("K8S_TOKEN_REQUEST_INVALID", "invalid json response") from exc
    status = body.get("status") if isinstance(body, dict) else None
    if not isinstance(status, dict):
        raise K8sTokenRequestError("K8S_TOKEN_REQUEST_INVALID", "missing status in response")
    token = status.get("token")
    if not isinstance(token, str) or not token:
        raise K8sTokenRequestError("K8S_TOKEN_REQUEST_INVALID", "missing token in response")
    ttl = status.get("expirationTimestamp")
    return TokenRequestResult(token=token, expiration_seconds=expiration_seconds)
