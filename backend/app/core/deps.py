"""
FastAPI 公共 Depends 依赖：用户认证 + 权限校验。
使用 JWT + Redis 黑名单，恒定时间比较。
"""
import hmac
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User

security_scheme = HTTPBearer()

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="无法验证凭据",
)


async def get_redis() -> AsyncGenerator[Redis, None]:
    r = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


async def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except Exception:
        raise _UNAUTHORIZED from None

    if payload.get("type") != "access" or payload.get("requires_2fa"):
        raise _UNAUTHORIZED

    jti = payload.get("jti")
    if not jti:
        raise _UNAUTHORIZED
    blacklisted = await redis.get(f"jwt:blacklist:{jti}")
    if blacklisted:
        raise _UNAUTHORIZED

    user_id = payload.get("sub")
    if not user_id:
        raise _UNAUTHORIZED

    result = await db.execute(select(User).where(User.id == int(user_id)))
    db_user = result.scalar_one_or_none()
    if not db_user or not db_user.is_active:
        raise _UNAUTHORIZED

    issued_at = _coerce_timestamp(payload.get("iat"))
    password_changed_at = _coerce_datetime(getattr(db_user, "password_changed_at", None))
    if not issued_at or (password_changed_at and issued_at < password_changed_at):
        raise _UNAUTHORIZED

    return {
        "id": db_user.id,
        "username": db_user.username,
        "tenant_id": db_user.tenant_id if hasattr(db_user, "tenant_id") else "default",
        "permissions": payload.get("permissions", []),
    }


def require_permission(perm: str) -> Callable[[dict[str, Any]], Any]:
    async def checker(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if perm not in user.get("permissions", []):
            raise HTTPException(status_code=403, detail=f"缺少权限: {perm}")
        return user

    return checker


def timing_safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


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
