"""P1 security regression tests for MFA, JWT revocation, and asset authorization."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.deps import current_user, get_redis
from app.core.security import (
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
)
from app.main import app
from app.models.user import User
from app.services import asset as asset_service_module
from app.services.asset import AssetService
from app.services.auth import AuthService


class ScalarResult:
    def __init__(self, value: Any = None, values: list[Any] | None = None) -> None:
        self.value = value
        self.values = values or ([] if value is None else [value])

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self.values


class FakeDB:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    async def execute(self, _statement: Any) -> ScalarResult:
        return ScalarResult(self.value)


class FakeRedis:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


class RaceyRedis(FakeRedis):
    """Simulate concurrent consumers where a pre-check GET may be stale."""

    async def get(self, key: str) -> str | None:
        return None


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def user(**overrides: Any) -> User:
    data = {
        "id": 1,
        "username": "alice",
        "display_name": "Alice",
        "email": "alice@example.com",
        "password_hash": "unused",
        "is_active": True,
        "is_superuser": False,
        "totp_enabled": False,
        "password_changed_at": datetime.now(UTC) - timedelta(minutes=5),
    }
    data.update(overrides)
    return User(**data)


def install_auth_dependencies(db_user: User, redis: FakeRedis | None = None) -> None:
    app.dependency_overrides[get_db] = lambda: FakeDB(db_user)
    app.dependency_overrides[get_redis] = lambda: redis or FakeRedis()


def test_mfa_challenge_token_cannot_access_protected_api(monkeypatch: pytest.MonkeyPatch) -> None:
    mfa_user = user(totp_enabled=True)
    install_auth_dependencies(mfa_user)

    async def authenticate(_db: Any, _username: str, _password: str) -> User:
        return mfa_user

    monkeypatch.setattr(AuthService, "authenticate", authenticate)

    with TestClient(app) as client:
        login_response = client.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
        assert login_response.status_code == 200
        body = login_response.json()
        assert body["requires_2fa"] is True
        assert body["access_token"] == ""

        me_response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {body['two_fa_token']}"},
        )

    assert me_response.status_code == 401


def test_access_tokens_include_jti_and_blacklisted_jti_is_rejected() -> None:
    token = create_access_token({"sub": "1", "username": "alice"})
    payload = decode_token(token)
    assert payload["jti"]

    install_auth_dependencies(user(), FakeRedis({f"jwt:blacklist:{payload['jti']}": "1"}))

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_access_token_issued_before_password_change_is_rejected() -> None:
    token = create_access_token({"sub": "1", "username": "alice"})
    install_auth_dependencies(user(password_changed_at=datetime.now(UTC) + timedelta(seconds=1)))

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_mfa_challenge_token_is_single_use(monkeypatch: pytest.MonkeyPatch) -> None:
    mfa_user = user(totp_enabled=True)
    install_auth_dependencies(mfa_user, FakeRedis())

    async def verify_totp(_db: Any, _user_id: int, _code: str) -> bool:
        return True

    monkeypatch.setattr(AuthService, "verify_totp", verify_totp)
    challenge = create_mfa_token({"sub": "1", "username": "alice"})

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": challenge, "totp_code": "123456"},
        )
        second = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": challenge, "totp_code": "123456"},
        )

    assert first.status_code == 200
    assert second.status_code == 401


def test_mfa_challenge_token_consume_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    mfa_user = user(totp_enabled=True)
    install_auth_dependencies(mfa_user, RaceyRedis())

    async def verify_totp(_db: Any, _user_id: int, _code: str) -> bool:
        return True

    monkeypatch.setattr(AuthService, "verify_totp", verify_totp)
    challenge = create_mfa_token({"sub": "1", "username": "alice"})

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": challenge, "totp_code": "123456"},
        )
        second = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": challenge, "totp_code": "123456"},
        )

    assert first.status_code == 200
    assert second.status_code == 401


def test_blacklisted_refresh_token_is_rejected() -> None:
    token = create_refresh_token({"sub": "1", "username": "alice"})
    payload = decode_token(token)
    install_auth_dependencies(user(), FakeRedis({f"jwt:blacklist:{payload['jti']}": "1"}))

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/token/refresh", params={"refresh_token": token})

    assert response.status_code == 401


def test_refresh_token_issued_before_password_change_is_rejected() -> None:
    token = create_refresh_token({"sub": "1", "username": "alice"})
    install_auth_dependencies(user(password_changed_at=datetime.now(UTC) + timedelta(seconds=1)))

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/token/refresh", params={"refresh_token": token})

    assert response.status_code == 401


def test_asset_routes_require_asset_permissions() -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "username": "alice",
        "permissions": [],
    }
    app.dependency_overrides[get_db] = lambda: FakeDB()

    with TestClient(app) as client:
        list_response = client.get("/api/v1/assets/")
        platform_response = client.get("/api/v1/assets/platforms")
        connection_response = client.post(
            "/api/v1/assets/test-connection",
            params={"address": "8.8.8.8", "port": 443},
        )

    assert list_response.status_code == 403
    assert platform_response.status_code == 403
    assert connection_response.status_code == 403


def test_test_connection_rejects_arbitrary_address_not_in_allowlist() -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "username": "alice",
        "permissions": ["assets:test"],
    }
    app.dependency_overrides[get_db] = lambda: FakeDB()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assets/test-connection",
            params={"address": "8.8.8.8", "port": 443},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_connection_blocks_hostnames_that_resolve_to_private_ips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asset_service_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(asset_service_module.socket.AF_INET, 0, 0, "", ("127.0.0.1", 22))],
    )

    result = await AssetService.test_connection("metadata.internal", 80)

    assert result == {"reachable": False, "error": "SSRF protection: private/internal IP blocked"}


@pytest.mark.asyncio
async def test_connection_uses_validated_resolved_ip_without_second_dns_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets: list[tuple[str, int]] = []

    class FakeSocket:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        asset_service_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                asset_service_module.socket.AF_INET,
                asset_service_module.socket.SOCK_STREAM,
                0,
                "",
                ("8.8.8.8", 443),
            )
        ],
    )

    def fake_create_connection(target: tuple[str, int], timeout: float) -> FakeSocket:
        targets.append(target)
        return FakeSocket()

    monkeypatch.setattr(asset_service_module.socket, "create_connection", fake_create_connection)

    result = await AssetService.test_connection("public.example.test", 443)

    assert result == {"reachable": True, "error": ""}
    assert targets == [("8.8.8.8", 443)]
