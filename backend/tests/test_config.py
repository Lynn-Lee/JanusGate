"""Configuration fail-closed regression tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_secret_key_must_be_present_and_long_enough() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(SECRET_KEY="too-short", _env_file=None)
