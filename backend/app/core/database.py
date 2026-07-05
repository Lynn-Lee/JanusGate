"""
SQLAlchemy 2.0 async 数据库引擎与会话管理。
"""
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, settings


class AsyncEngineFactory(Protocol):
    def __call__(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class DatabaseEngines:
    writer: Any
    reader: Any


def create_database_engines(
    *,
    settings: Settings,
    engine_factory: AsyncEngineFactory = create_async_engine,
) -> DatabaseEngines:
    engine_kwargs: dict[str, Any] = {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_pre_ping": True,
        "echo": settings.APP_ENV == "development",
    }
    writer = engine_factory(settings.DATABASE_URL, **engine_kwargs)
    if not settings.DATABASE_READ_REPLICA_URL.strip():
        return DatabaseEngines(writer=writer, reader=writer)

    reader = engine_factory(settings.DATABASE_READ_REPLICA_URL, **engine_kwargs)
    return DatabaseEngines(writer=writer, reader=reader)


database_engines = create_database_engines(settings=settings)
engine: AsyncEngine = database_engines.writer
read_engine: AsyncEngine = database_engines.reader

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

ReadAsyncSessionLocal = async_sessionmaker(
    bind=read_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    async with ReadAsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
