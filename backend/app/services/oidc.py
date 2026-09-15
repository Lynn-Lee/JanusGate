"""OIDC 登录：discovery、PKCE、token 交换、safe_next_url（#t76）。

安全约束：
- 强制 HTTPS（Issuer / 到 IdP 的请求均 verify SSL，禁止关闭）
- 授权码 + mandatory PKCE (S256) + 完整 state
- 回调 next 经 safe_next_url，拒绝 `//` 开放重定向
- 仅按邮箱绑定已有用户；无账号 → 无法登录（不自动建户）
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_field, encrypt_field
from app.models.oidc import OidcProvider
from app.models.user import User

DEFAULT_SCOPES = "openid profile email"
OIDC_LOGIN_FAIL = "无法登录"
ISSUER_MUST_HTTPS = "Issuer 必须是 HTTPS"
STATE_REDIS_PREFIX = "oidc:state:"
TICKET_REDIS_PREFIX = "oidc:ticket:"
STATE_TTL_SECONDS = 600
TICKET_TTL_SECONDS = 120


def safe_next_url(raw: str | None, *, default: str = "/assets") -> str:
    """仅允许站内相对路径，拒绝 `//evil` 等开放重定向。"""
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    if not value.startswith("/") or value.startswith("//"):
        return default
    if "\\" in value or "://" in value:
        return default
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return default
    return value


def validate_issuer_url(issuer_url: str) -> str:
    cleaned = (issuer_url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("Issuer URL 不能为空")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError(ISSUER_MUST_HTTPS)
    return cleaned


def is_fully_configured(provider: OidcProvider | None) -> bool:
    if provider is None:
        return False
    if not provider.enabled:
        return False
    if not (provider.display_name or "").strip():
        return False
    try:
        validate_issuer_url(provider.issuer_url)
    except ValueError:
        return False
    if not (provider.client_id or "").strip():
        return False
    return bool((provider.client_secret_encrypted or "").strip())


def build_callback_url(request_base_url: str | None = None) -> str:
    base = (settings.PUBLIC_API_BASE_URL or "").strip().rstrip("/")
    if not base and request_base_url:
        base = request_base_url.rstrip("/")
    if not base:
        base = "http://localhost:8000"
    return f"{base}/api/v1/auth/oidc/callback"


def frontend_base_url() -> str:
    configured = (settings.FRONTEND_BASE_URL or "").strip().rstrip("/")
    if configured:
        return configured
    origins = settings.CORS_ORIGINS or []
    if origins:
        return str(origins[0]).rstrip("/")
    return "http://localhost:5173"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OidcService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        # verify=True 为默认；显式传入，禁止关闭 SSL。
        return httpx.AsyncClient(
            timeout=15.0,
            verify=True,
            follow_redirects=False,
            transport=self._transport,
        )

    async def get_provider(self, db: AsyncSession, tenant_id: str) -> OidcProvider | None:
        result = await db.execute(
            select(OidcProvider).where(OidcProvider.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def upsert_settings(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        enabled: bool,
        display_name: str,
        issuer_url: str,
        client_id: str,
        client_secret: str | None,
        scopes: str,
    ) -> OidcProvider:
        display_name = (display_name or "").strip()
        client_id = (client_id or "").strip()
        scopes_value = (scopes or "").strip() or DEFAULT_SCOPES
        issuer_cleaned = (issuer_url or "").strip()

        if issuer_cleaned:
            issuer_cleaned = validate_issuer_url(issuer_cleaned)
        elif enabled:
            raise ValueError(ISSUER_MUST_HTTPS)

        provider = await self.get_provider(db, tenant_id)
        secret_to_store: str | None = None
        if client_secret is not None and client_secret.strip():
            secret_to_store = encrypt_field(client_secret.strip())

        if provider is None:
            if enabled and not secret_to_store:
                raise ValueError("Client Secret 不能为空")
            provider = OidcProvider(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                enabled=enabled,
                display_name=display_name,
                issuer_url=issuer_cleaned,
                client_id=client_id,
                client_secret_encrypted=secret_to_store or "",
                scopes=scopes_value,
            )
            db.add(provider)
        else:
            provider.enabled = enabled
            provider.display_name = display_name
            provider.issuer_url = issuer_cleaned
            provider.client_id = client_id
            provider.scopes = scopes_value
            if secret_to_store is not None:
                provider.client_secret_encrypted = secret_to_store

        if enabled:
            if not display_name:
                raise ValueError("显示名称不能为空")
            if not issuer_cleaned:
                raise ValueError(ISSUER_MUST_HTTPS)
            if not client_id:
                raise ValueError("Client ID 不能为空")
            if not (provider.client_secret_encrypted or "").strip():
                raise ValueError("Client Secret 不能为空")

        await db.commit()
        await db.refresh(provider)
        return provider

    async def discover(self, issuer_url: str) -> dict[str, Any]:
        issuer = validate_issuer_url(issuer_url)
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        async with self._client() as client:
            response = await client.get(discovery_url)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("OIDC discovery 响应无效")
        for key in ("authorization_endpoint", "token_endpoint"):
            if not data.get(key):
                raise ValueError(f"OIDC discovery 缺少 {key}")
            endpoint = str(data[key])
            if not endpoint.startswith("https://"):
                raise ValueError("OIDC 端点必须是 HTTPS")
        return data

    async def build_authorization_redirect(
        self,
        *,
        provider: OidcProvider,
        redis: Any,
        callback_url: str,
        next_url: str,
    ) -> str:
        discovery = await self.discover(provider.issuer_url)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = _pkce_pair()
        payload = {
            "tenant_id": provider.tenant_id,
            "provider_id": provider.id,
            "code_verifier": verifier,
            "nonce": nonce,
            "next": safe_next_url(next_url),
            "callback_url": callback_url,
        }
        await redis.set(
            f"{STATE_REDIS_PREFIX}{state}",
            json.dumps(payload),
            ex=STATE_TTL_SECONDS,
        )
        params = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": callback_url,
            "scope": provider.scopes or DEFAULT_SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{discovery['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        provider: OidcProvider,
        code: str,
        code_verifier: str,
        callback_url: str,
    ) -> dict[str, Any]:
        discovery = await self.discover(provider.issuer_url)
        secret = decrypt_field(provider.client_secret_encrypted)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback_url,
            "client_id": provider.client_id,
            "client_secret": secret,
            "code_verifier": code_verifier,
        }
        async with self._client() as client:
            response = await client.post(
                str(discovery["token_endpoint"]),
                data=data,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_payload = response.json()
        if not isinstance(token_payload, dict):
            raise ValueError("token 响应无效")
        email = self._email_from_token_payload(token_payload)
        if not email and discovery.get("userinfo_endpoint"):
            email = await self._email_from_userinfo(
                str(discovery["userinfo_endpoint"]),
                str(token_payload.get("access_token") or ""),
            )
        if not email:
            raise ValueError("缺少邮箱")
        return {"email": email.lower().strip(), "raw": token_payload}

    def _email_from_token_payload(self, token_payload: dict[str, Any]) -> str:
        id_token = token_payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            return ""
        try:
            parts = id_token.split(".")
            if len(parts) < 2:
                return ""
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            email = claims.get("email")
            return str(email).strip() if email else ""
        except Exception:
            return ""

    async def _email_from_userinfo(self, endpoint: str, access_token: str) -> str:
        if not endpoint.startswith("https://") or not access_token:
            return ""
        async with self._client() as client:
            response = await client.get(
                endpoint,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            return ""
        email = data.get("email")
        return str(email).strip() if email else ""

    async def find_user_by_email(
        self, db: AsyncSession, *, tenant_id: str, email: str
    ) -> User | None:
        normalized = email.lower().strip()
        if not normalized:
            return None
        result = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == normalized,
                User.is_active.is_(True),
            )
        )
        user = result.scalar_one_or_none()
        if user is not None:
            return user
        # 兼容大小写不一致的存量邮箱
        result = await db.execute(
            select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True))
        )
        for candidate in result.scalars().all():
            if (candidate.email or "").lower().strip() == normalized:
                return candidate
        return None

    async def store_login_ticket(self, redis: Any, payload: dict[str, Any]) -> str:
        ticket = secrets.token_urlsafe(32)
        await redis.set(
            f"{TICKET_REDIS_PREFIX}{ticket}",
            json.dumps(payload),
            ex=TICKET_TTL_SECONDS,
        )
        return ticket

    async def consume_login_ticket(self, redis: Any, ticket: str) -> dict[str, Any] | None:
        key = f"{TICKET_REDIS_PREFIX}{ticket}"
        raw = await self._redis_getdel(redis, key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def consume_state(self, redis: Any, state: str) -> dict[str, Any] | None:
        key = f"{STATE_REDIS_PREFIX}{state}"
        raw = await self._redis_getdel(redis, key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    async def _redis_getdel(self, redis: Any, key: str) -> str | None:
        getdel = getattr(redis, "getdel", None)
        if callable(getdel):
            value = await getdel(key)
            if value is None:
                return None
            return value.decode() if isinstance(value, bytes) else str(value)
        value = await redis.get(key)
        delete = getattr(redis, "delete", None)
        if callable(delete):
            await delete(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)


oidc_service = OidcService()
