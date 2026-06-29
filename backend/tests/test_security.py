"""Security regression tests for password, token, and field encryption helpers."""
from __future__ import annotations

import jwt

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decrypt_field,
    encrypt_field,
    hash_password,
    password_policy_violations,
    verify_password,
)


def test_password_policy_requires_complex_password() -> None:
    violations = password_policy_violations("short")

    assert "密码长度不能少于 8 位" in violations
    assert "必须包含至少 1 个大写字母" in violations
    assert "必须包含至少 1 个数字" in violations
    assert "必须包含至少 1 个特殊字符" in violations
    assert password_policy_violations("Stronger-Password-123") == []


def test_password_hash_verification_round_trip_and_rejects_wrong_password() -> None:
    password_hash = hash_password("Stronger-Password-123")

    assert password_hash != "Stronger-Password-123"
    assert verify_password("Stronger-Password-123", password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert not verify_password("Stronger-Password-123", "not-a-valid-bcrypt-hash")


def test_access_and_refresh_tokens_carry_expected_type() -> None:
    access_token = create_access_token({"sub": "user-1", "permissions": ["sessions:read"]})
    refresh_token = create_refresh_token({"sub": "user-1"})

    access_payload = jwt.decode(
        access_token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    refresh_payload = jwt.decode(
        refresh_token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )

    assert access_payload["type"] == "access"
    assert access_payload["sub"] == "user-1"
    assert access_payload["permissions"] == ["sessions:read"]
    assert refresh_payload["type"] == "refresh"
    assert refresh_payload["sub"] == "user-1"


def test_field_encryption_uses_randomized_aead_and_round_trips() -> None:
    plaintext = "root-password-123"

    ciphertext_a = encrypt_field(plaintext)
    ciphertext_b = encrypt_field(plaintext)

    assert ciphertext_a != plaintext
    assert ciphertext_b != plaintext
    assert ciphertext_a != ciphertext_b
    assert decrypt_field(ciphertext_a) == plaintext
    assert decrypt_field(ciphertext_b) == plaintext
