"""#t68 K8s namespace 与凭据校验单元测试。"""

from __future__ import annotations

import pytest

from app.k8s.service import resolve_namespace_scope, validate_k8s_account_fields
from app.k8s.validation import intersect_namespaces, validate_api_server, validate_k8s_name
from app.models.account import Account
from app.models.k8s_cluster import K8sClusterModel


def test_validate_k8s_name_accepts_dns_label() -> None:
    validate_k8s_name("default", field="namespace")


def test_validate_k8s_name_rejects_uppercase() -> None:
    with pytest.raises(ValueError, match="namespace_INVALID"):
        validate_k8s_name("Default", field="namespace")


def test_intersect_namespaces_requires_overlap() -> None:
    allowed = intersect_namespaces(["default", "ops"], ["ops", "staging"])
    assert allowed == frozenset({"ops"})


def test_intersect_namespaces_empty_when_no_overlap() -> None:
    with pytest.raises(ValueError, match="K8S_NAMESPACE_SCOPE_EMPTY"):
        intersect_namespaces(["default"], ["kube-system"])


def test_validate_k8s_account_fields_requires_token_for_k8s_protocol() -> None:
    with pytest.raises(ValueError, match="K8S_TOKEN_SECRET_REQUIRED"):
        validate_k8s_account_fields(
            protocol="k8s",
            secret_id="",
            k8s_namespaces=["default"],
            k8s_service_account="default",
            k8s_token_ttl_seconds=3600,
        )


def test_resolve_namespace_scope_uses_cluster_and_account_intersection() -> None:
    cluster = K8sClusterModel(
        tenant_id="tenant-a",
        asset_id=1,
        api_server="https://k8s.example:6443",
        server_ca_pem="pem",
        namespaces_json='["default", "ops", "staging"]',
    )
    account = Account(
        tenant_id="tenant-a",
        asset_id=1,
        username="sa",
        protocol="k8s",
        secret_id="sec",
        k8s_namespaces_json='["ops", "staging"]',
    )
    scope = resolve_namespace_scope(cluster, account)
    assert scope == frozenset({"ops", "staging"})


def test_validate_api_server_requires_https() -> None:
    with pytest.raises(ValueError, match="K8S_API_SERVER_MUST_BE_HTTPS"):
        validate_api_server("http://k8s.example:6443")
