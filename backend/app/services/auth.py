"""认证服务：登录、MFA、API Key 管理。"""
import hashlib
import secrets
import uuid
from datetime import UTC, datetime

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    decrypt_field,
    encrypt_field,
    hash_password,
    password_policy_violations,
    verify_password,
)
from app.models.user import ApiKey, User


class AuthService:

    @staticmethod
    async def authenticate(db: AsyncSession, username: str, password: str) -> User | None:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    @staticmethod
    async def create_user(
        db: AsyncSession, username: str, password: str, email: str = ""
    ) -> User:
        violations = password_policy_violations(password)
        if violations:
            raise ValueError("; ".join(violations))
        user = User(
            username=username,
            display_name=username,
            email=email,
            password_hash=hash_password(password),
            password_changed_at=datetime.now(UTC),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def change_password(
        db: AsyncSession, user_id: int, old_password: str, new_password: str
    ) -> None:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("用户不存在")
        if not verify_password(old_password, user.password_hash):
            raise ValueError("当前密码错误")
        if old_password == new_password:
            raise ValueError("新密码不能与当前密码相同")
        violations = password_policy_violations(new_password)
        if violations:
            raise ValueError("; ".join(violations))
        user.password_hash = hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        await db.commit()

    @staticmethod
    async def setup_totp(db: AsyncSession, user_id: int) -> dict:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("用户不存在")
        secret = pyotp.random_base32()
        user.totp_secret = encrypt_field(secret)
        await db.commit()
        totp = pyotp.TOTP(secret)
        return {
            "secret": secret,
            "provisioning_uri": totp.provisioning_uri(
                name=user.username, issuer_name="JanusGate"
            ),
        }

    @staticmethod
    async def verify_totp(db: AsyncSession, user_id: int, code: str) -> bool:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.totp_secret:
            return False
        secret = decrypt_field(user.totp_secret)
        return pyotp.TOTP(secret).verify(code, valid_window=1)

    @staticmethod
    async def enable_totp(db: AsyncSession, user_id: int, code: str) -> None:
        if not await AuthService.verify_totp(db, user_id, code):
            raise ValueError("TOTP 验证码错误")
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("用户不存在")
        user.totp_enabled = True
        await db.commit()

    @staticmethod
    async def disable_totp(db: AsyncSession, user_id: int, code: str) -> None:
        if not await AuthService.verify_totp(db, user_id, code):
            raise ValueError("TOTP 验证码错误")
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("用户不存在")
        user.totp_enabled = False
        user.totp_secret = None
        await db.commit()

    @staticmethod
    async def create_api_key(db: AsyncSession, user_id: int, name: str) -> dict:
        key_id = uuid.uuid4().hex
        secret = secrets.token_hex(24)
        secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        api_key = ApiKey(key_id=key_id, secret_hash=secret_hash, name=name, user_id=user_id)
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)
        return {"key_id": key_id, "secret": secret, "name": name, "created_at": api_key.created_at.isoformat()}

    @staticmethod
    async def verify_api_key(db: AsyncSession, key_id: str, secret: str) -> User | None:
        secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.key_id == key_id,
                ApiKey.secret_hash == secret_hash,
                ApiKey.is_active.is_(True),
            )
        )
        api_key = result.scalar_one_or_none()
        if not api_key:
            return None
        api_key.last_used_at = datetime.now(UTC)
        await db.commit()
        return api_key.user
