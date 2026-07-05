"""Database engine routing regression tests."""
from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.database import create_database_engines


def test_create_database_engines_defaults_read_engine_to_writer() -> None:
    created_urls: list[str] = []

    def fake_create_engine(url: str, **_: Any) -> str:
        created_urls.append(url)
        return f"engine:{url}"

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        DATABASE_URL="postgresql+asyncpg://writer/janusgate",
        DATABASE_READ_REPLICA_URL="",
        _env_file=None,
    )

    engines = create_database_engines(settings=settings, engine_factory=fake_create_engine)

    assert engines.writer == "engine:postgresql+asyncpg://writer/janusgate"
    assert engines.reader == engines.writer
    assert created_urls == ["postgresql+asyncpg://writer/janusgate"]


def test_create_database_engines_uses_configured_read_replica() -> None:
    created_urls: list[str] = []

    def fake_create_engine(url: str, **_: Any) -> str:
        created_urls.append(url)
        return f"engine:{url}"

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        DATABASE_URL="postgresql+asyncpg://writer/janusgate",
        DATABASE_READ_REPLICA_URL="postgresql+asyncpg://reader/janusgate",
        _env_file=None,
    )

    engines = create_database_engines(settings=settings, engine_factory=fake_create_engine)

    assert engines.writer == "engine:postgresql+asyncpg://writer/janusgate"
    assert engines.reader == "engine:postgresql+asyncpg://reader/janusgate"
    assert created_urls == [
        "postgresql+asyncpg://writer/janusgate",
        "postgresql+asyncpg://reader/janusgate",
    ]
