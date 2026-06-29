"""
FastAPI 公共 Depends 依赖：用户认证 + 权限校验。
使用 JWT + Redis 黑名单，恒定时间比较。
"""
import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token

security_scheme = HTTPBearer()

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="无法验证凭据",
)


async def get_redis() -> Redis:
    r = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


async def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except Exception:
        raise _UNAUTHORIZED from None

    if payload.get("type") != "access":
        raise _UNAUTHORIZED

    jti = payload.get("jti", "")
    if jti:
        blacklisted = await redis.get(f"jwt:blacklist:{jti}")
        if blacklisted:
            raise _UNAUTHORIZED

    user_id = payload.get("sub")
    if not user_id:
        raise _UNAUTHORIZED

    return {
        "id": user_id,
        "username": payload.get("username", ""),
        "tenant_id": payload.get("tenant_id", "default"),
        "permissions": payload.get("permissions", []),
    }


def require_permission(perm: str):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if perm not in user.get("permissions", []):
            raise HTTPException(status_code=403, detail=f"缺少权限: {perm}")
        return user

    return checker


def timing_safe_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
