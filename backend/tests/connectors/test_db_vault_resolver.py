"""#t71 DatabaseVaultSessionConnectionResolver 测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.db_vault_resolver import (
    CallableDbSecretUnwrapper,
    DatabaseVaultSessionConnectionResolver,
)
from app.connectors.routing_resolver import RoutingSessionConnectionResolver
from app.connectors.session_runtime import ConnectorSessionMode
from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_database_asset(session_factory: async_sessionmaker[AsyncSession]) -> Asset:
    async with session_factory() as session:
        platform = Platform(name="PostgreSQL", category="database", protocols='["postgresql"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="prod-pg",
            address="10.0.0.20",
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="database",
            port=5432,
            is_active=True,
        )
        session.add(asset)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="app_user",
                protocol="postgresql",
                secret_id="sec_pg",
                status="active",
            )
        )
        await session.commit()
        await session.refresh(asset)
        return asset


def _dispatch(asset_id: int) -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id="sess-1",
        connector_id="conn-1",
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id=str(asset_id),
        account_id="app_user",
        protocol="postgresql",
    )


@pytest.mark.asyncio
async def test_db_resolver_returns_postgresql_spec(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_database_asset(session_factory)
    resolver = DatabaseVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=CallableDbSecretUnwrapper(lambda secret_id: "db-pass"),
    )
    spec = await resolver.resolve(_dispatch(asset.id))
    assert spec.mode is ConnectorSessionMode.DB_POSTGRESQL
    assert spec.db is not None
    assert spec.db.target.host == "10.0.0.20"
    assert spec.db.target.username == "app_user"
    assert spec.db.credential.password == "db-pass"


@pytest.mark.asyncio
async def test_routing_resolver_delegates_postgresql_protocol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_database_asset(session_factory)

    class DenySsh:
        async def resolve(self, request: ConnectorDispatchRequest):
            raise PermissionError("SSH_SHOULD_NOT_RUN")

    class DenyK8s:
        async def resolve(self, request: ConnectorDispatchRequest):
            raise RuntimeError("K8S_SHOULD_NOT_RUN")

    db_resolver = DatabaseVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=CallableDbSecretUnwrapper(lambda secret_id: "db-pass"),
    )
    routing = RoutingSessionConnectionResolver(
        ssh_resolver=DenySsh(),
        k8s_resolver=DenyK8s(),
        db_resolver=db_resolver,
    )
    spec = await routing.resolve(_dispatch(asset.id))
    assert spec.mode is ConnectorSessionMode.DB_POSTGRESQL
