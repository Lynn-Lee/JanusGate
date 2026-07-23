"""Test bootstrap for JanusGate backend.

The application validates SECRET_KEY at import time. Tests provide a deterministic
non-production key so local/unit test runs do not depend on a developer .env file.
"""
from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 48)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://janusgate:janusgate@localhost:5432/janusgate",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.audits.service import audit_service, repository  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.observability.metrics import metrics_registry  # noqa: E402


@pytest.fixture(autouse=True)
def reset_app_state() -> None:
    repository.clear()
    metrics_registry.reset()
    app.dependency_overrides.clear()
    yield
    repository.clear()
    metrics_registry.reset()
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def audit_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """给每个测试一套隔离的内存 sqlite 审计账本（StaticPool 单连接共享）。

    AuditService 自管读写会话，这里把它的读/写 session_factory 都换成本引擎的工厂，
    从而与各测试自己的 `get_db`/`get_read_db` 覆盖完全解耦——审计读写永远落在本引擎，
    等价于旧版「随处可用的内存审计仓库」。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    original_write = audit_service._session_factory
    original_read = audit_service._read_session_factory
    audit_service._session_factory = factory
    audit_service._read_session_factory = factory
    try:
        yield factory
    finally:
        audit_service._session_factory = original_write
        audit_service._read_session_factory = original_read
        await engine.dispose()
