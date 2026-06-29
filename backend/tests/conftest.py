"""Test bootstrap for JanusGate backend.

The application validates SECRET_KEY at import time. Tests provide a deterministic
non-production key so local/unit test runs do not depend on a developer .env file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 48)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://janusgate:janusgate@localhost:5432/janusgate",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
