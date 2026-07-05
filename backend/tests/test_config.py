"""Configuration fail-closed regression tests."""
from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_secret_key_must_be_present_and_long_enough() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(SECRET_KEY="too-short", _env_file=None)


def test_vault_local_kms_master_key_is_configurable() -> None:
    encoded_key = base64.urlsafe_b64encode(b"k" * 32).decode()

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        VAULT_LOCAL_KMS_MASTER_KEY=encoded_key,
        _env_file=None,
    )

    assert encoded_key == settings.VAULT_LOCAL_KMS_MASTER_KEY


def test_redis_sentinel_mode_requires_sentinel_urls() -> None:
    with pytest.raises(ValidationError, match="REDIS_SENTINEL_URLS"):
        Settings(
            SECRET_KEY="test-secret-key-test-secret-key-32",
            REDIS_MODE="sentinel",
            _env_file=None,
        )


def test_redis_cluster_mode_requires_cluster_urls() -> None:
    with pytest.raises(ValidationError, match="REDIS_CLUSTER_URLS"):
        Settings(
            SECRET_KEY="test-secret-key-test-secret-key-32",
            REDIS_MODE="cluster",
            _env_file=None,
        )
