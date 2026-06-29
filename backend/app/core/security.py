"""
安全模块：JWT 签发/验证、密码哈希、AES-256-GCM 字段加密。
所有加密默认使用 AEAD 模式，禁止 ECB。
"""
import base64
import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

# ── 密码策略 ──
PASSWORD_MIN_LENGTH = 8
PASSWORD_EXPIRE_DAYS = 90
BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def password_policy_violations(password: str) -> list[str]:
    violations: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        violations.append(f"密码长度不能少于 {PASSWORD_MIN_LENGTH} 位")
    if not re.search(r"[A-Z]", password):
        violations.append("必须包含至少 1 个大写字母")
    if not re.search(r"[a-z]", password):
        violations.append("必须包含至少 1 个小写字母")
    if not re.search(r"\d", password):
        violations.append("必须包含至少 1 个数字")
    if not re.search(r"[^A-Za-z0-9]", password):
        violations.append("必须包含至少 1 个特殊字符")
    return violations


# ── JWT ──

def create_access_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload.update({
        "exp": datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
    })
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload.update({
        "exp": datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
    })
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


# ── AES-256-GCM 字段加密（替代 JumpServer 的 SM4/AES-ECB）──

def _get_aes_key() -> bytes:
    return hashlib.sha256(settings.SECRET_KEY.encode()).digest()


def encrypt_field(value: str) -> str:
    if not value:
        return value
    key = _get_aes_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, value.encode(), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_field(value: str) -> str:
    if not value:
        return value
    try:
        key = _get_aes_key()
        raw = base64.urlsafe_b64decode(value.encode())
        nonce, ciphertext = raw[:12], raw[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None).decode()
    except Exception:
        return value
