"""Alembic 运行环境。

连接串优先取 Alembic `-x db_url=...` 参数，否则回落到应用 `Settings.DATABASE_URL`；
支持 async 驱动（生产 asyncpg、离线自检 aiosqlite）。导入 ``app.models`` 是为了把
全部 ORM 表注册到 ``Base.metadata``，autogenerate 才能看到完整 schema。
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401  注册全部 ORM 表到 Base.metadata
from alembic import context
from app.core.config import settings
from app.core.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """解析目标数据库连接串：`-x db_url=` 优先，否则用应用配置。"""
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("db_url") or settings.DATABASE_URL


def run_migrations_offline() -> None:
    """离线（--sql）模式：只按 URL 生成 SQL，不建立实际连接。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """在给定同步连接上执行迁移（供 async run_sync 回调）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：用 async 引擎连接目标库并执行迁移。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
