"""#t79 泄露密码库：SHA-256 比对，明文只在请求内存中出现。"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import password_policy_violations
from app.models.governance import LeakPassword
from app.services.tenant_settings import password_min_length_for_tenant, weak_password_check_enabled

LEAKED_PASSWORD_REJECTED = "密码出现在泄露密码库中，请更换"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# 内置样本均为能通过复杂度策略、但属于常见泄露口令的值；测试账号口令不要放进来。
_BUILTIN_PLAINTEXTS: tuple[str, ...] = (
    "Password1!",
    "Welcome1!",
    "P@ssw0rd",
    "Admin123!",
    "Qwerty1!",
)
BUILTIN_LEAK_SHA256: frozenset[str] = frozenset(
    hashlib.sha256(item.encode("utf-8")).hexdigest() for item in _BUILTIN_PLAINTEXTS
)


def leak_password_sha256(password: str) -> str:
    """对密码做 SHA-256。只 strip 两端空白，保留大小写，避免把不同口令打成同一哈希。"""

    return hashlib.sha256(password.strip().encode("utf-8")).hexdigest()


def normalize_sha256(value: str) -> str:
    digest = value.strip().lower()
    if not _SHA256_HEX.fullmatch(digest):
        raise ValueError("LEAK_PASSWORD_HASH_INVALID")
    return digest


async def password_is_leaked(db: AsyncSession, *, tenant_id: str, password: str) -> bool:
    digest = leak_password_sha256(password)
    if digest in BUILTIN_LEAK_SHA256:
        return True
    result = await db.execute(
        select(LeakPassword.id).where(
            LeakPassword.tenant_id == tenant_id,
            LeakPassword.sha256 == digest,
        )
    )
    return result.scalar_one_or_none() is not None


async def reject_if_leaked(db: AsyncSession, *, tenant_id: str, password: str) -> None:
    """弱密码检查关闭时跳过库比对，复杂度策略仍由调用方执行。"""

    if not await weak_password_check_enabled(db, tenant_id):
        return
    if await password_is_leaked(db, tenant_id=tenant_id, password=password):
        raise ValueError(LEAKED_PASSWORD_REJECTED)


async def enforce_new_password(db: AsyncSession, *, tenant_id: str, password: str) -> None:
    """复杂度 + 租户最小长度 overlay + 泄露库。失败抛出 ValueError，文案可给用户看。"""

    min_length = await password_min_length_for_tenant(db, tenant_id)
    violations = password_policy_violations(password, min_length=min_length)
    if violations:
        raise ValueError("; ".join(violations))
    await reject_if_leaked(db, tenant_id=tenant_id, password=password)
