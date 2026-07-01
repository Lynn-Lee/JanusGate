"""Phase 3 API contract and error-response regression tests."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.workflows.routes import get_workflow_service
from app.core.deps import current_user
from app.main import app
from app.services.auth import AuthService
from tests.workflows.test_workflow_service_and_api import build_workflow_service


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def async_value(value: Any) -> Any:
    async def inner(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return inner


def test_openapi_exposes_error_response_contract_for_frontend_clients() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    error_schema = spec["components"]["schemas"]["ErrorResponse"]
    assert error_schema["required"] == ["code", "message", "request_id"]
    assert set(error_schema["properties"]) >= {"code", "message", "detail", "request_id"}

    login_responses = spec["paths"]["/api/v1/auth/login"]["post"]["responses"]
    for status_code in ["400", "401", "403", "404", "422"]:
        assert login_responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_http_exception_uses_unified_shape_and_preserves_legacy_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AuthService, "authenticate", async_value(None))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json() == {
        "code": "UNAUTHORIZED",
        "message": "用户名或密码错误",
        "detail": "用户名或密码错误",
        "request_id": "",
    }


def test_request_validation_errors_use_frontend_parseable_contract() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login", json={"username": "alice"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "请求参数校验失败"
    assert body["request_id"] == ""
    assert isinstance(body["detail"], list)
    assert body["detail"][0]["loc"] == ["body", "password"]


def test_workflow_uppercase_error_detail_is_promoted_to_stable_error_code() -> None:
    service, _audit, _revoker = build_workflow_service()
    app.dependency_overrides[get_workflow_service] = lambda: service
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "tenant-1",
        "permissions": ["workflow:approve"],
    }
    try:
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/workflows/requests",
                json={
                    "asset_id": "asset-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "reason": "数据库故障排查",
                    "requested_ttl_seconds": 1800,
                },
            ).status_code == 201
            assert client.post("/api/v1/workflows/requests/wr-1/submit").status_code == 200
            response = client.post(
                "/api/v1/workflows/requests/wr-1/approve",
                json={"decision_reason": "自己审批自己", "grant_ttl_seconds": 1800},
            )

        assert response.status_code == 403
        assert response.json()["detail"] == "SELF_APPROVAL_NOT_ALLOWED"
        assert response.json()["code"] == "SELF_APPROVAL_NOT_ALLOWED"
    finally:
        app.dependency_overrides.clear()
