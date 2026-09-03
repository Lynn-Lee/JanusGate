"""#t68 K8s namespace 与凭据校验工具。"""

from __future__ import annotations

import json
import re

_K8S_NAME_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_HTTPS_PREFIX = "https://"


def load_namespaces(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def validate_k8s_name(value: str, *, field: str) -> None:
    if not value or len(value) > 253 or not _K8S_NAME_PATTERN.match(value):
        raise ValueError(f"{field}_INVALID")


def validate_api_server(api_server: str) -> None:
    normalized = api_server.strip()
    if not normalized.startswith(_HTTPS_PREFIX):
        raise ValueError("K8S_API_SERVER_MUST_BE_HTTPS")
    if len(normalized) > 512:
        raise ValueError("K8S_API_SERVER_TOO_LONG")


def validate_server_ca(server_ca_pem: str) -> None:
    if not server_ca_pem.strip():
        raise ValueError("K8S_SERVER_CA_REQUIRED")
    if "BEGIN CERTIFICATE" not in server_ca_pem:
        raise ValueError("K8S_SERVER_CA_INVALID")


def intersect_namespaces(
    cluster_namespaces: list[str], account_namespaces: list[str]
) -> frozenset[str]:
    """计算账号与集群两层 namespace 授权的交集。"""

    if not account_namespaces:
        raise ValueError("K8S_ACCOUNT_NAMESPACES_REQUIRED")
    cluster_set = (
        frozenset(cluster_namespaces) if cluster_namespaces else frozenset(account_namespaces)
    )
    allowed = cluster_set.intersection(account_namespaces)
    if not allowed:
        raise ValueError("K8S_NAMESPACE_SCOPE_EMPTY")
    for name in allowed:
        validate_k8s_name(name, field="namespace")
    return allowed


def validate_token_ttl(seconds: int) -> None:
    if seconds < 600 or seconds > 86400:
        raise ValueError("K8S_TOKEN_TTL_OUT_OF_RANGE")
