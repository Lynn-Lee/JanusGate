"""#t76 OIDC login / settings in-process coverage."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import oidc as oidc_api
from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.core.security import encrypt_field
from app.main import app
from app.models.oidc import OidcProvider
from app.models.user import User
from app.services.oidc import (
    ISSUER_MUST_HTTPS,
    OIDC_LOGIN_FAIL,
    OidcService,
    safe_next_url,
    validate_issuer_url,
)


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
        if getattr(obj, "id", None) in (None, ""):
            obj.id = "oidc-1"


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        del ex
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


def provider(**overrides: Any) -> OidcProvider:
    base = {
        "id": "oidc-1",
        "tenant_id": "default",
        "enabled": True,
        "display_name": "公司 IdP",
        "issuer_url": "https://idp.example.com",
        "client_id": "client-1",
        "client_secret_encrypted": encrypt_field("super-secret"),
        "scopes": "openid profile email",
    }
    base.update(overrides)
    return OidcProvider(**base)


def user(**overrides: Any) -> User:
    base = {
        "id": 1,
        "username": "alice",
        "display_name": "Alice",
        "email": "alice@example.test",
        "password_hash": "hashed",
        "is_active": True,
        "is_superuser": False,
        "totp_enabled": False,
        "tenant_id": "default",
    }
    base.update(overrides)
    return User(**base)


def install_db(fake_db: FakeDB) -> None:
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_read_db] = lambda: fake_db


def install_redis(fake_redis: FakeRedis | None = None) -> FakeRedis:
    redis = fake_redis or FakeRedis()
    app.dependency_overrides[get_redis] = lambda: redis
    return redis


def install_admin() -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": 1,
        "username": "admin",
        "tenant_id": "default",
        "permissions": ["admin"],
    }


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def allow_login_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _allow(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(oidc_api, "_enforce_login_acl", _allow)


@pytest.fixture(autouse=True)
def stub_token_data(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _token_data(db: Any, u: User, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        del db
        payload = {
            "sub": str(u.id),
            "username": u.username,
            "tenant_id": getattr(u, "tenant_id", None) or "default",
            "permissions": ["assets:read"],
            "menu_permissions": [],
            "role_ids": [],
        }
        if extra:
            payload.update(extra)
        return payload

    monkeypatch.setattr(oidc_api, "_token_data_for_user", _token_data)


def _id_token_with_email(email: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def mock_idp_transport(*, email: str = "alice@example.test") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://idp.example.com/authorize",
                    "token_endpoint": "https://idp.example.com/token",
                    "userinfo_endpoint": "https://idp.example.com/userinfo",
                },
            )
        if url.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "at-1",
                    "id_token": _id_token_with_email(email),
                    "token_type": "Bearer",
                },
            )
        if url.endswith("/userinfo"):
            return httpx.Response(200, json={"email": email})
        return httpx.Response(404, text="missing")

    return httpx.MockTransport(handler)


def test_safe_next_url_rejects_open_redirect() -> None:
    assert safe_next_url("/assets") == "/assets"
    assert safe_next_url("//evil.example/phish") == "/assets"
    assert safe_next_url("https://evil.example/") == "/assets"
    assert safe_next_url("/\\evil") == "/assets"
    assert safe_next_url(None) == "/assets"


def test_validate_issuer_rejects_non_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_issuer_url("http://idp.example.com")
    assert validate_issuer_url("https://idp.example.com/") == "https://idp.example.com"


def test_settings_get_and_update_never_echo_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    install_admin()
    existing = provider()
    install_db(FakeDB(ScalarResult(existing), ScalarResult(existing)))
    with TestClient(app) as client:
        got = client.get("/api/v1/auth/oidc/settings")
        assert got.status_code == 200
        body = got.json()
        assert "client_secret" not in body
        assert body["client_secret_configured"] is True
        assert body["display_name"] == "公司 IdP"
        assert body["callback_url"].endswith("/api/v1/auth/oidc/callback")

    # update keep secret when empty
    db = FakeDB(ScalarResult(existing))
    install_db(db)
    with TestClient(app) as client:
        resp = client.put(
            "/api/v1/auth/oidc/settings",
            json={
                "enabled": True,
                "display_name": "公司 IdP",
                "issuer_url": "https://idp.example.com",
                "client_id": "client-1",
                "client_secret": "",
                "scopes": "openid profile email",
            },
        )
    assert resp.status_code == 200
    assert "client_secret" not in resp.json()
    assert resp.json()["client_secret_configured"] is True
    # empty client_secret keeps previously stored ciphertext
    assert (existing.client_secret_encrypted or "").strip()


def test_settings_reject_http_issuer_on_save() -> None:
    install_admin()
    install_db(FakeDB(ScalarResult(None)))
    with TestClient(app) as client:
        resp = client.put(
            "/api/v1/auth/oidc/settings",
            json={
                "enabled": True,
                "display_name": "IdP",
                "issuer_url": "http://idp.example.com",
                "client_id": "c1",
                "client_secret": "s1",
                "scopes": "openid email",
            },
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == ISSUER_MUST_HTTPS


def test_login_options_hidden_when_incomplete() -> None:
    incomplete = provider(enabled=True, client_secret_encrypted="")
    install_db(FakeDB(ScalarResult(incomplete)))
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/oidc/login-options")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "display_name": ""}

    install_db(FakeDB(ScalarResult(provider())))
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/oidc/login-options")
    assert resp.json() == {"enabled": True, "display_name": "公司 IdP"}


def test_start_redirect_includes_pkce_and_state(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB(ScalarResult(provider())))
    redis = install_redis()
    service = OidcService(transport=mock_idp_transport())
    monkeypatch.setattr(oidc_api, "oidc_service", service)

    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/auth/oidc/start",
            params={"next": "/assets"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://idp.example.com/authorize?")
    qs = parse_qs(urlparse(location).query)
    assert qs["response_type"] == ["code"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]
    state = qs["state"][0]
    assert any(k.endswith(state) for k in redis.store)
    stored = json.loads(next(v for k, v in redis.store.items() if k.endswith(state)))
    assert "code_verifier" in stored
    assert stored["next"] == "/assets"


def test_start_rejects_open_redirect_in_stored_next(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(FakeDB(ScalarResult(provider())))
    redis = install_redis()
    monkeypatch.setattr(oidc_api, "oidc_service", OidcService(transport=mock_idp_transport()))
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/auth/oidc/start",
            params={"next": "//evil.example"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    stored = json.loads(next(v for k, v in redis.store.items() if k.endswith(state)))
    assert stored["next"] == "/assets"


def test_callback_unknown_email_cannot_login(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(
        FakeDB(
            ScalarResult(provider()),
            ScalarResult(None),  # email exact
            ScalarResult(values=[]),  # fallback scan
        )
    )
    redis = install_redis()
    service = OidcService(transport=mock_idp_transport(email="missing@example.test"))
    monkeypatch.setattr(oidc_api, "oidc_service", service)
    redis.store["oidc:state:st1"] = json.dumps(
        {
            "tenant_id": "default",
            "provider_id": "oidc-1",
            "code_verifier": "verifier",
            "nonce": "n",
            "next": "/assets",
            "callback_url": "http://testserver/api/v1/auth/oidc/callback",
        }
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/auth/oidc/callback",
            params={"code": "c1", "state": "st1"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "error=" in resp.headers["location"]
    assert OIDC_LOGIN_FAIL in unquote(resp.headers["location"])


def test_callback_login_acl_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    async def _deny(*_a: Any, **_k: Any) -> None:
        raise HTTPException(status_code=403, detail="当前无法登录")

    monkeypatch.setattr(oidc_api, "_enforce_login_acl", _deny)
    install_db(
        FakeDB(
            ScalarResult(provider()),
            ScalarResult(user()),
        )
    )
    redis = install_redis()
    monkeypatch.setattr(oidc_api, "oidc_service", OidcService(transport=mock_idp_transport()))
    redis.store["oidc:state:st2"] = json.dumps(
        {
            "tenant_id": "default",
            "provider_id": "oidc-1",
            "code_verifier": "verifier",
            "nonce": "n",
            "next": "/assets",
            "callback_url": "http://testserver/api/v1/auth/oidc/callback",
        }
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/auth/oidc/callback",
            params={"code": "c1", "state": "st2"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert OIDC_LOGIN_FAIL in unquote(resp.headers["location"])


def test_callback_success_exchange_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    install_db(
        FakeDB(
            ScalarResult(provider()),
            ScalarResult(user()),
        )
    )
    redis = install_redis()
    monkeypatch.setattr(oidc_api, "oidc_service", OidcService(transport=mock_idp_transport()))
    monkeypatch.setattr(oidc_api, "create_access_token", lambda payload: f"access:{payload['sub']}")
    monkeypatch.setattr(oidc_api, "create_refresh_token", lambda payload: f"refresh:{payload['sub']}")
    redis.store["oidc:state:st3"] = json.dumps(
        {
            "tenant_id": "default",
            "provider_id": "oidc-1",
            "code_verifier": "verifier",
            "nonce": "n",
            "next": "/assets",
            "callback_url": "http://testserver/api/v1/auth/oidc/callback",
        }
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/auth/oidc/callback",
            params={"code": "c1", "state": "st3"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        ticket = parse_qs(urlparse(loc).query)["ticket"][0]
        exchanged = client.post("/api/v1/auth/oidc/exchange", json={"ticket": ticket})
    assert exchanged.status_code == 200
    body = exchanged.json()
    assert body["access_token"] == "access:1"
    assert body["refresh_token"] == "refresh:1"
    assert body["requires_2fa"] is False


def test_pkce_challenge_is_s256() -> None:
    from app.services import oidc as oidc_svc

    verifier, challenge = oidc_svc._pkce_pair()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected
