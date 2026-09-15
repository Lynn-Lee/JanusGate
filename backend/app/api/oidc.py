"""OIDC 设置与登录路由（#t76）。"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

# Re-exported helpers used by auth.py login completion
from app.api.auth import (  # noqa: E402
    _enforce_login_acl,
    _token_data_for_user,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.core.security import create_access_token, create_mfa_token, create_refresh_token
from app.schemas.auth import TokenResponse
from app.schemas.oidc import (
    OidcExchangeRequest,
    OidcLoginOptionResponse,
    OidcSettingsResponse,
    OidcSettingsUpdate,
)
from app.services.oidc import (
    DEFAULT_SCOPES,
    OIDC_LOGIN_FAIL,
    build_callback_url,
    frontend_base_url,
    is_fully_configured,
    oidc_service,
    safe_next_url,
)
from app.tenancy.scope import actor_scope_from_user

router = APIRouter(prefix="/auth/oidc", tags=["OIDC"])


def _require_admin(user: dict[str, Any]) -> None:
    if "admin" not in user.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少权限: admin")


def _settings_response(provider: Any, *, request: Request) -> OidcSettingsResponse:
    callback = build_callback_url(str(request.base_url).rstrip("/"))
    if provider is None:
        return OidcSettingsResponse(
            enabled=False,
            display_name="",
            issuer_url="",
            client_id="",
            client_secret_configured=False,
            scopes=DEFAULT_SCOPES,
            callback_url=callback,
        )
    return OidcSettingsResponse(
        enabled=bool(provider.enabled),
        display_name=provider.display_name or "",
        issuer_url=provider.issuer_url or "",
        client_id=provider.client_id or "",
        client_secret_configured=bool((provider.client_secret_encrypted or "").strip()),
        scopes=provider.scopes or DEFAULT_SCOPES,
        callback_url=callback,
    )


def _fail_redirect() -> RedirectResponse:
    target = f"{frontend_base_url()}/login?error={quote(OIDC_LOGIN_FAIL)}"
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


@router.get("/settings", response_model=OidcSettingsResponse)
async def get_oidc_settings(
    request: Request,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> OidcSettingsResponse:
    _require_admin(user)
    tenant_id = actor_scope_from_user(user).tenant_id
    provider = await oidc_service.get_provider(db, tenant_id)
    return _settings_response(provider, request=request)


@router.put("/settings", response_model=OidcSettingsResponse)
async def update_oidc_settings(
    data: OidcSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> OidcSettingsResponse:
    _require_admin(user)
    tenant_id = actor_scope_from_user(user).tenant_id
    try:
        provider = await oidc_service.upsert_settings(
            db,
            tenant_id=tenant_id,
            enabled=data.enabled,
            display_name=data.display_name,
            issuer_url=data.issuer_url,
            client_id=data.client_id,
            client_secret=data.client_secret,
            scopes=data.scopes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _settings_response(provider, request=request)


@router.get("/login-options", response_model=OidcLoginOptionResponse)
async def oidc_login_options(
    db: AsyncSession = Depends(get_read_db),
    tenant_id: str = Query(default="default", max_length=64),
) -> OidcLoginOptionResponse:
    provider = await oidc_service.get_provider(db, tenant_id or "default")
    if provider is None or not is_fully_configured(provider):
        return OidcLoginOptionResponse(enabled=False, display_name="")
    return OidcLoginOptionResponse(enabled=True, display_name=provider.display_name)


@router.get("/start")
async def oidc_start(
    request: Request,
    next_url: str = Query("/assets", alias="next", max_length=512),
    tenant_id: str = Query(default="default", max_length=64),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    provider = await oidc_service.get_provider(db, tenant_id or "default")
    if provider is None or not is_fully_configured(provider):
        return _fail_redirect()
    callback_url = build_callback_url(str(request.base_url).rstrip("/"))
    try:
        url = await oidc_service.build_authorization_redirect(
            provider=provider,
            redis=redis,
            callback_url=callback_url,
            next_url=next_url,
        )
    except Exception:
        return _fail_redirect()
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    if error or not code or not state:
        return _fail_redirect()
    state_payload = await oidc_service.consume_state(redis, state)
    if not state_payload:
        return _fail_redirect()
    tenant_id = str(state_payload.get("tenant_id") or "default")
    provider = await oidc_service.get_provider(db, tenant_id)
    if provider is None or not is_fully_configured(provider):
        return _fail_redirect()
    callback_url = str(state_payload.get("callback_url") or build_callback_url(str(request.base_url).rstrip("/")))
    try:
        exchanged = await oidc_service.exchange_code(
            provider=provider,
            code=code,
            code_verifier=str(state_payload.get("code_verifier") or ""),
            callback_url=callback_url,
        )
        user = await oidc_service.find_user_by_email(
            db, tenant_id=tenant_id, email=str(exchanged["email"])
        )
        if user is None:
            return _fail_redirect()
        await _enforce_login_acl(db, user, request)
    except HTTPException as exc:
        if exc.status_code == 403:
            return _fail_redirect()
        return _fail_redirect()
    except Exception:
        return _fail_redirect()

    next_path = safe_next_url(str(state_payload.get("next") or "/assets"))
    if user.totp_enabled:
        ticket_payload = {
            "kind": "mfa",
            "two_fa_token": create_mfa_token({"sub": str(user.id), "username": user.username}),
            "next": next_path,
        }
    else:
        token_data = await _token_data_for_user(db, user)
        ticket_payload = {
            "kind": "tokens",
            "access_token": create_access_token(token_data),
            "refresh_token": create_refresh_token(token_data),
            "next": next_path,
        }
    ticket = await oidc_service.store_login_ticket(redis, ticket_payload)
    query = urlencode({"ticket": ticket})
    return RedirectResponse(
        url=f"{frontend_base_url()}/login?{query}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/exchange", response_model=TokenResponse)
async def oidc_exchange(
    data: OidcExchangeRequest,
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    payload = await oidc_service.consume_login_ticket(redis, data.ticket)
    if not payload:
        raise HTTPException(status_code=401, detail=OIDC_LOGIN_FAIL)
    kind = payload.get("kind")
    if kind == "mfa":
        return TokenResponse(
            requires_2fa=True,
            two_fa_token=str(payload.get("two_fa_token") or ""),
        )
    if kind == "tokens":
        return TokenResponse(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=str(payload.get("refresh_token") or ""),
        )
    raise HTTPException(status_code=401, detail=OIDC_LOGIN_FAIL)
