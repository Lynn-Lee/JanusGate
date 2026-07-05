"""Auth API, dependency, and service regression coverage."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis, require_permission
from app.core.security import create_access_token, create_refresh_token
from app.main import app
from app.models.user import ApiKey, User
from app.services import auth as auth_service_module
from app.services.auth import AuthService


class ScalarResult:
    def __init__(self, value: Any = None, values: list[Any] | None = None) -> None:
        self.value = value
        self.values = values if values is not None else ([] if value is None else [value])

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self.values


class FakeDB:
    def __init__(self, *results: ScalarResult) -> None:
        self.results = list(results)
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0

    async def execute(self, _statement: Any) -> ScalarResult:
        if not self.results:
            raise AssertionError("unexpected execute call")
        return self.results.pop(0)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = 1
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)


class FakeRedis:
    def __init__(self, value: str | None = None, set_result: bool = True) -> None:
        self.value = value
        self.set_result = set_result

    async def get(self, key: str) -> str | None:
        self.last_key = key
        return self.value

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self.last_set = (key, value, ex, nx)
        return self.set_result


def user(**overrides: Any) -> User:
    base = {
        "id": 1,
        "username": "alice",
        "display_name": "Alice",
        "email": "alice@example.test",
        "password_hash": "hashed-old",
        "is_active": True,
        "is_superuser": False,
        "totp_enabled": False,
        "totp_secret": None,
    }
    base.update(overrides)
    return User(**base)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def install_db(fake_db: FakeDB) -> None:
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_read_db] = lambda: fake_db


def install_redis(fake_redis: FakeRedis | None = None) -> FakeRedis:
    redis = fake_redis or FakeRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    return redis


def install_user(payload: dict[str, Any] | None = None) -> None:
    app.dependency_overrides[current_user] = lambda: payload or {
        "id": 1,
        "username": "alice",
        "permissions": ["assets:read"],
    }


def async_value(value: Any) -> Any:
    async def inner(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return inner


def async_raise(error: Exception) -> Any:
    async def inner(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return inner


def test_login_returns_access_and_refresh_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB())
    monkeypatch.setattr(AuthService, "authenticate", async_value(user()))
    monkeypatch.setattr(auth_api, "create_access_token", lambda payload: f"access:{payload['sub']}")
    monkeypatch.setattr(auth_api, "create_refresh_token", lambda payload: f"refresh:{payload['sub']}")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "correct-password"},
        )

    assert response.status_code == 200
    assert response.json()["access_token"] == "access:1"
    assert response.json()["refresh_token"] == "refresh:1"
    assert response.json()["requires_2fa"] is False


def test_login_requires_2fa_when_totp_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB())
    monkeypatch.setattr(AuthService, "authenticate", async_value(user(totp_enabled=True)))
    monkeypatch.setattr(auth_api, "create_mfa_token", lambda payload: f"mfa:{payload['sub']}")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "correct-password"},
        )

    assert response.status_code == 200
    assert response.json()["requires_2fa"] is True
    assert response.json()["two_fa_token"] == "mfa:1"
    assert response.json()["access_token"] == ""


def test_login_rejects_bad_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB())
    monkeypatch.setattr(AuthService, "authenticate", async_value(None))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_login_2fa_exchanges_valid_challenge_for_session_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB(ScalarResult(user(totp_enabled=True))))
    redis = install_redis()
    issued_access_payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(auth_api, "decode_token", lambda _token: {"sub": "1", "type": "mfa", "jti": "mfa-jti", "requires_2fa": True})
    monkeypatch.setattr(AuthService, "verify_totp", async_value(True))
    monkeypatch.setattr(
        auth_api,
        "create_access_token",
        lambda payload: issued_access_payloads.append(payload) or f"access:{payload['2fa_verified']}",
    )
    monkeypatch.setattr(auth_api, "create_refresh_token", lambda payload: f"refresh:{payload['sub']}")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": "challenge", "totp_code": "123456"},
        )

    assert response.status_code == 200
    assert response.json()["access_token"] == "access:True"
    assert response.json()["refresh_token"] == "refresh:1"
    assert redis.last_set == ("mfa:challenge:consumed:mfa-jti", "1", 300, True)
    assert "assets:read" in issued_access_payloads[0]["permissions"]


def test_login_2fa_rejects_invalid_challenge_and_bad_totp(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB(ScalarResult(user(totp_enabled=True))))
    install_redis()
    monkeypatch.setattr(auth_api, "decode_token", lambda _token: {"sub": "1", "type": "access", "jti": "not-mfa", "requires_2fa": False})

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": "not-a-challenge", "totp_code": "123456"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "非法的 2FA 凭证"

    install_db(FakeDB(ScalarResult(user(totp_enabled=True))))
    install_redis()
    monkeypatch.setattr(auth_api, "decode_token", lambda _token: {"sub": "1", "type": "mfa", "jti": "mfa-jti", "requires_2fa": True})
    monkeypatch.setattr(AuthService, "verify_totp", async_value(False))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login/2fa",
            json={"two_fa_token": "challenge", "totp_code": "000000"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "TOTP 验证码错误"


def test_refresh_token_endpoint_rejects_access_token_and_disabled_user(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB())
    install_redis()
    monkeypatch.setattr(auth_api, "decode_token", lambda _token: {"type": "access", "sub": "1"})

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/token/refresh", params={"refresh_token": "access-token"})

    assert response.status_code == 401
    assert response.json()["detail"] == "非法的 token 类型"

    install_db(FakeDB(ScalarResult(user(is_active=False))))
    install_redis()
    monkeypatch.setattr(auth_api, "decode_token", lambda _token: {"type": "refresh", "sub": "1", "jti": "refresh-jti", "iat": datetime.now(UTC)})

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/token/refresh", params={"refresh_token": "refresh-token"})

    assert response.status_code == 401
    assert response.json()["detail"] == "用户不存在或已被禁用"


def test_refresh_token_endpoint_issues_new_token_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB(ScalarResult(user())))
    install_redis()
    monkeypatch.setattr(auth_api, "decode_token", lambda _token: {"type": "refresh", "sub": "1", "jti": "refresh-jti", "iat": datetime.now(UTC)})
    monkeypatch.setattr(auth_api, "create_access_token", lambda payload: f"new-access:{payload['username']}")
    monkeypatch.setattr(auth_api, "create_refresh_token", lambda payload: f"new-refresh:{payload['sub']}")

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/token/refresh", params={"refresh_token": "refresh-token"})

    assert response.status_code == 200
    assert response.json()["access_token"] == "new-access:alice"
    assert response.json()["refresh_token"] == "new-refresh:1"


def test_authenticated_profile_and_account_mutation_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_user()
    install_db(FakeDB(ScalarResult(user())))
    monkeypatch.setattr(AuthService, "setup_totp", async_value({"secret": "SECRET", "provisioning_uri": "otpauth://totp/JanusGate"}))
    monkeypatch.setattr(AuthService, "enable_totp", async_value(None))
    monkeypatch.setattr(AuthService, "disable_totp", async_value(None))
    monkeypatch.setattr(AuthService, "change_password", async_value(None))
    monkeypatch.setattr(AuthService, "create_api_key", async_value({"key_id": "kid", "secret": "secret", "name": "ci"}))

    with TestClient(app) as client:
        assert client.get("/api/v1/auth/me").json()["username"] == "alice"
        assert client.post("/api/v1/auth/2fa/setup").json()["secret"] == "SECRET"
        assert client.post("/api/v1/auth/2fa/verify", json={"totp_code": "123456"}).json()["status"] == "ok"
        assert client.post("/api/v1/auth/2fa/disable", json={"totp_code": "123456"}).json()["status"] == "ok"
        assert client.post(
            "/api/v1/auth/password/change",
            json={"old_password": "OldPass-123", "new_password": "NewPass-123"},
        ).json()["status"] == "ok"
        assert client.post("/api/v1/auth/apikeys", json={"name": "ci"}).json()["key_id"] == "kid"


def test_account_mutation_routes_surface_service_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    install_user()
    install_db(FakeDB())
    monkeypatch.setattr(AuthService, "setup_totp", async_raise(ValueError("用户不存在")))
    monkeypatch.setattr(AuthService, "enable_totp", async_raise(ValueError("TOTP 验证码错误")))
    monkeypatch.setattr(AuthService, "disable_totp", async_raise(ValueError("TOTP 验证码错误")))
    monkeypatch.setattr(AuthService, "change_password", async_raise(ValueError("当前密码错误")))

    with TestClient(app) as client:
        assert client.post("/api/v1/auth/2fa/setup").status_code == 400
        assert client.post("/api/v1/auth/2fa/verify", json={"totp_code": "123456"}).status_code == 400
        assert client.post("/api/v1/auth/2fa/disable", json={"totp_code": "123456"}).status_code == 400
        assert client.post(
            "/api/v1/auth/password/change",
            json={"old_password": "bad", "new_password": "NewPass-123"},
        ).status_code == 400


@pytest.mark.asyncio
async def test_current_user_rejects_refresh_tokens_blacklisted_tokens_and_missing_subject() -> None:
    refresh_credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_refresh_token({"sub": "1"}),
    )
    with pytest.raises(HTTPException) as refresh_error:
        await current_user(refresh_credentials, FakeDB(), FakeRedis())
    assert refresh_error.value.status_code == 401

    blacklisted_credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_access_token({"sub": "1", "jti": "revoked-token"}),
    )
    with pytest.raises(HTTPException) as blacklist_error:
        await current_user(blacklisted_credentials, FakeDB(), FakeRedis("1"))
    assert blacklist_error.value.status_code == 401

    missing_sub_credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_access_token({"username": "alice"}),
    )
    with pytest.raises(HTTPException) as missing_sub_error:
        await current_user(missing_sub_credentials, FakeDB(), FakeRedis())
    assert missing_sub_error.value.status_code == 401


@pytest.mark.asyncio
async def test_current_user_returns_active_user_and_rejects_disabled_user() -> None:
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_access_token({"sub": "1", "permissions": ["assets:read"]}),
    )

    authenticated = await current_user(credentials, FakeDB(ScalarResult(user())), FakeRedis())

    assert authenticated == {
        "id": 1,
        "username": "alice",
        "tenant_id": "default",
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": ["assets:read"],
    }

    with pytest.raises(HTTPException) as disabled_error:
        await current_user(credentials, FakeDB(ScalarResult(user(is_active=False))), FakeRedis())
    assert disabled_error.value.status_code == 401


@pytest.mark.asyncio
async def test_require_permission_allows_present_permission_and_denies_missing() -> None:
    assert await require_permission("assets:read")({"permissions": ["assets:read"]}) == {"permissions": ["assets:read"]}

    with pytest.raises(HTTPException) as forbidden:
        await require_permission("assets:write")({"permissions": ["assets:read"]})
    assert forbidden.value.status_code == 403


@pytest.mark.asyncio
async def test_auth_service_authenticate_and_create_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service_module, "verify_password", lambda plain, hashed: plain == "correct" and hashed == "hashed")
    monkeypatch.setattr(auth_service_module, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(auth_service_module, "password_policy_violations", lambda password: ["weak"] if password == "weak" else [])

    assert await AuthService.authenticate(FakeDB(ScalarResult(None)), "missing", "correct") is None
    assert await AuthService.authenticate(FakeDB(ScalarResult(user(is_active=False))), "alice", "correct") is None
    assert await AuthService.authenticate(FakeDB(ScalarResult(user(password_hash="hashed"))), "alice", "wrong") is None
    assert await AuthService.authenticate(FakeDB(ScalarResult(user(password_hash="hashed"))), "alice", "correct") is not None

    with pytest.raises(ValueError, match="weak"):
        await AuthService.create_user(FakeDB(), "alice", "weak")

    db = FakeDB()
    created = await AuthService.create_user(db, "alice", "Stronger-123")
    assert created in db.added
    assert created.password_hash == "hashed:Stronger-123"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_auth_service_password_totp_and_api_key_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service_module, "verify_password", lambda plain, hashed: plain == "old" and hashed == "hashed-old")
    monkeypatch.setattr(auth_service_module, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(auth_service_module, "password_policy_violations", lambda password: ["weak"] if password == "weak" else [])
    monkeypatch.setattr(auth_service_module, "encrypt_field", lambda value: f"encrypted:{value}")
    monkeypatch.setattr(auth_service_module, "decrypt_field", lambda value: value.removeprefix("encrypted:"))

    with pytest.raises(ValueError, match="用户不存在"):
        await AuthService.change_password(FakeDB(ScalarResult(None)), 1, "old", "NewPass-123")
    with pytest.raises(ValueError, match="当前密码错误"):
        await AuthService.change_password(FakeDB(ScalarResult(user())), 1, "bad", "NewPass-123")
    with pytest.raises(ValueError, match="新密码不能与当前密码相同"):
        await AuthService.change_password(FakeDB(ScalarResult(user())), 1, "old", "old")
    with pytest.raises(ValueError, match="weak"):
        await AuthService.change_password(FakeDB(ScalarResult(user())), 1, "old", "weak")

    change_user = user()
    await AuthService.change_password(FakeDB(ScalarResult(change_user)), 1, "old", "NewPass-123")
    assert change_user.password_hash == "hashed:NewPass-123"

    with pytest.raises(ValueError, match="用户不存在"):
        await AuthService.setup_totp(FakeDB(ScalarResult(None)), 1)

    setup_user = user()
    setup = await AuthService.setup_totp(FakeDB(ScalarResult(setup_user)), 1)
    assert setup["secret"]
    assert setup_user.totp_secret is not None
    assert "otpauth://" in setup["provisioning_uri"]

    assert not await AuthService.verify_totp(FakeDB(ScalarResult(user(totp_secret=None))), 1, "123456")

    async def fake_verify_totp(_db: Any, _user_id: int, code: str) -> bool:
        return code == "123456"

    monkeypatch.setattr(AuthService, "verify_totp", fake_verify_totp)
    with pytest.raises(ValueError, match="TOTP 验证码错误"):
        await AuthService.enable_totp(FakeDB(), 1, "000000")
    with pytest.raises(ValueError, match="用户不存在"):
        await AuthService.enable_totp(FakeDB(ScalarResult(None)), 1, "123456")

    totp_user = user(totp_secret="encrypted:SECRET")
    await AuthService.enable_totp(FakeDB(ScalarResult(totp_user)), 1, "123456")
    assert totp_user.totp_enabled is True
    await AuthService.disable_totp(FakeDB(ScalarResult(totp_user)), 1, "123456")
    assert totp_user.totp_enabled is False
    assert totp_user.totp_secret is None

    key_db = FakeDB()
    key = await AuthService.create_api_key(key_db, 1, "ci")
    assert key["name"] == "ci"
    assert key["secret"]
    assert isinstance(key_db.added[0], ApiKey)

    api_key = key_db.added[0]
    api_key.user = user()
    verified = await AuthService.verify_api_key(FakeDB(ScalarResult(api_key)), api_key.key_id, key["secret"])
    assert verified is api_key.user
    assert api_key.last_used_at is not None
    assert await AuthService.verify_api_key(FakeDB(ScalarResult(None)), "missing", "bad-secret") is None
