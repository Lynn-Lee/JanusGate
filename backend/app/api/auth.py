"""认证 API 路由。"""
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.core.security import (
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
)
from app.models.tenancy import DEFAULT_TENANT_TIMEZONE, Tenant
from app.models.user import User
from app.rbac.repository import ensure_default_user_binding, ensure_superuser_binding
from app.rbac.resolver import RbacResolver
from app.schemas.auth import (
    ChangePasswordRequest,
    CreateApiKeyRequest,
    Login2FARequest,
    LoginRequest,
    TokenResponse,
    TwoFASetupResponse,
    TwoFAVerifyRequest,
    UserDirectoryItem,
    UserDirectoryListResponse,
    UserMeResponse,
)
from app.services.auth import AuthService
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(prefix="/auth", tags=["认证"])
users_router = APIRouter(tags=["用户"])

MVP_CONSOLE_PERMISSIONS = ("assets:read", "sessions:connect")
ADMIN_CONSOLE_PERMISSIONS = (
    "admin",
    "assets:read",
    "assets:write",
    "assets:test",
    "audit:read",
    "audit:write",
    "sessions:connect",
    "workflow:approve",
    "workflow:audit",
    "workflow:admin",
)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = await AuthService.authenticate(db, data.username, data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    await _enforce_login_acl(db, user, request)

    if user.totp_enabled:
        return TokenResponse(
            requires_2fa=True,
            two_fa_token=create_mfa_token({"sub": str(user.id), "username": user.username}),
        )

    token_data = await _token_data_for_user(db, user)
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/login/2fa", response_model=TokenResponse)
async def login_2fa(
    data: Login2FARequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    try:
        payload = decode_token(data.two_fa_token)
    except Exception:
        raise HTTPException(status_code=401, detail="2FA 凭证无效或已过期") from None
    if payload.get("type") != "mfa" or not payload.get("requires_2fa"):
        raise HTTPException(status_code=401, detail="非法的 2FA 凭证")
    jti = cast(str | None, payload.get("jti"))
    if not jti:
        raise HTTPException(status_code=401, detail="非法的 2FA 凭证")
    consumed_key = f"mfa:challenge:consumed:{jti}"
    if not await redis.set(consumed_key, "1", ex=300, nx=True):
        raise HTTPException(status_code=401, detail="2FA 凭证无效或已过期")
    user_id = int(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")
    if not await AuthService.verify_totp(db, user.id, data.totp_code):
        raise HTTPException(status_code=400, detail="TOTP 验证码错误")
    await _enforce_login_acl(db, user, request)
    token_data = await _token_data_for_user(db, user, extra={"2fa_verified": True})
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/token/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(
    refresh_token: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="refresh_token 无效或已过期") from None
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="非法的 token 类型")
    jti = cast(str | None, payload.get("jti"))
    if not jti:
        raise HTTPException(status_code=401, detail="refresh_token 无效或已过期")
    if await redis.get(f"jwt:blacklist:{jti}"):
        raise HTTPException(status_code=401, detail="refresh_token 无效或已过期")
    user_id = cast(str, payload.get("sub"))
    result = await db.execute(select(User).where(cast(Any, User.id) == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")
    issued_at = _coerce_timestamp(payload.get("iat"))
    password_changed_at = _coerce_datetime(user.password_changed_at)
    if not issued_at or (password_changed_at and issued_at < password_changed_at):
        raise HTTPException(status_code=401, detail="refresh_token 无效或已过期")
    token_data = await _token_data_for_user(db, user)
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.get("/me", response_model=UserMeResponse)
async def get_me(
    user: dict[str, Any] = Depends(current_user), db: AsyncSession = Depends(get_read_db)
) -> UserMeResponse:
    result = await db.execute(select(User).where(User.id == user["id"]))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(404, "用户不存在")
    timezone = DEFAULT_TENANT_TIMEZONE
    try:
        tenant_id = str(getattr(db_user, "tenant_id", None) or user.get("tenant_id") or "default")
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is not None and str(tenant.timezone or "").strip():
            timezone = str(tenant.timezone)
    except Exception:
        timezone = DEFAULT_TENANT_TIMEZONE
    return UserMeResponse(
        id=db_user.id,
        username=db_user.username,
        display_name=db_user.display_name,
        email=db_user.email,
        is_superuser=db_user.is_superuser,
        totp_enabled=db_user.totp_enabled,
        permissions=list(user.get("permissions") or []),
        timezone=timezone,
    )


@router.post("/2fa/setup", response_model=TwoFASetupResponse)
async def setup_2fa(
    user: dict[str, Any] = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> TwoFASetupResponse:
    try:
        result = await AuthService.setup_totp(db, user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return TwoFASetupResponse(**result)


@router.post("/2fa/verify", response_model=dict)
async def verify_2fa(
    data: TwoFAVerifyRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    try:
        await AuthService.enable_totp(db, user["id"], data.totp_code)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "ok", "msg": "2FA 已启用"}


@router.post("/2fa/disable", response_model=dict)
async def disable_2fa(
    data: TwoFAVerifyRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    try:
        await AuthService.disable_totp(db, user["id"], data.totp_code)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "ok", "msg": "2FA 已禁用"}


@router.post("/password/change", response_model=dict)
async def change_password(
    data: ChangePasswordRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    try:
        await AuthService.change_password(db, user["id"], data.old_password, data.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "ok", "msg": "密码已修改"}


@router.post("/apikeys", response_model=dict)
async def create_api_key(
    data: CreateApiKeyRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await AuthService.create_api_key(db, user["id"], data.name)



@users_router.get("/users/", response_model=UserDirectoryListResponse)
async def list_tenant_users(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> UserDirectoryListResponse:
    """租户用户目录：LoginACL 与资产授权「谁能连」共用。不含密码哈希。"""
    _require_users_directory(user)
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(User, actor_scope)
        .where(User.is_active.is_(True))
        .order_by(User.username.asc(), User.id.asc())
    )
    users = list(result.scalars().all())
    items = [
        UserDirectoryItem(
            id=item.id,
            username=item.username,
            display_name=item.display_name or "",
        )
        for item in users
    ]
    return UserDirectoryListResponse(items=items, total=len(items))


def _require_users_directory(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or "acl:read" in permissions or "assets:read" in permissions:
        return
    raise HTTPException(status_code=404, detail="USERS_NOT_FOUND")



LOGIN_ACL_DENIED_COPY = "当前无法登录"


async def _enforce_login_acl(
    db: AsyncSession, user: User, request: Request | None = None
) -> None:
    """交互式登录 overlay。加载/判定失败 fail-closed。不作用于 API Key 路径。"""

    del request  # LoginACL 无 IP 条件；保留 Request 以便后续 overlay 扩展。
    from app.policy.repository import build_tenant_policy_service
    from app.policy.schemas import PolicyDecision
    from app.tenancy.scope import ActorScope

    tenant_id = getattr(user, "tenant_id", None) or "default"
    try:
        service = await build_tenant_policy_service(
            db, ActorScope(user_id=str(user.id), tenant_id=str(tenant_id))
        )
        decision = service.evaluate_login(str(user.id), str(tenant_id))
    except Exception:
        raise HTTPException(status_code=403, detail=LOGIN_ACL_DENIED_COPY) from None
    if decision.decision == PolicyDecision.DENY:
        raise HTTPException(status_code=403, detail=LOGIN_ACL_DENIED_COPY)


def _coerce_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, datetime):
        return _coerce_datetime(value)
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _token_data_for_user(
    db: AsyncSession,
    user: User,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tenant_id = getattr(user, "tenant_id", None) or "default"
    if user.is_superuser:
        await ensure_superuser_binding(db, tenant_id=tenant_id, user_id=str(user.id))
    else:
        await ensure_default_user_binding(db, tenant_id=tenant_id, user_id=str(user.id))

    actor_scope = ActorScope(user_id=str(user.id), tenant_id=tenant_id)
    effective = await RbacResolver.resolve(
        db,
        actor_scope=actor_scope,
        is_superuser=user.is_superuser,
    )
    token_data: dict[str, Any] = {
        "sub": str(user.id),
        "username": user.username,
        "tenant_id": tenant_id,
        "permissions": list(effective.permissions),
        "menu_permissions": list(effective.menu_permissions),
        "role_ids": list(effective.role_ids),
    }
    if extra:
        token_data.update(extra)
    return token_data
