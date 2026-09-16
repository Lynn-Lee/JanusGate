"""#t71 生产 SessionConnectionResolver：数据库协议分流。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.asset_vault_resolver import (
    AssetVaultSessionConnectionResolver,
    MappingSecretUnwrapper,
)
from app.connectors.host_key_trust import HostKeyTrustStore
from app.connectors.session_runtime import ConnectorSessionMode
from app.connectors.ssh_hostkey import HostKeyScan
from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform


class FakeScanner:
    def __init__(self) -> None:
        self.calls = 0

    async def scan(self, host: str, port: int) -> HostKeyScan:
        self.calls += 1
        return HostKeyScan(
            host=host,
            port=port,
            key_type="ssh-ed25519",
            public_key="ssh-ed25519 SHOULD-NOT-SCAN",
            fingerprint="SHA256:unused",
        )


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


async def _seed_database_asset(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    protocol: str,
    address: str,
    port: int,
    namespace: str = "",
    server_ca: str = "",
) -> Asset:
    async with session_factory() as session:
        platform = Platform(
            name=f"{protocol}-platform",
            category="database",
            asset_type="database",
            protocols=f'["{protocol}"]',
        )
        session.add(platform)
        await session.flush()
        asset = Asset(
            name=f"prod-{protocol}",
            address=address,
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="database",
            port=port,
            is_active=True,
            namespace=namespace,
            server_ca=server_ca,
        )
        session.add(asset)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="app_user",
                protocol=protocol,
                secret_id="sec_db",
                status="active",
            )
        )
        await session.commit()
        await session.refresh(asset)
        return asset


def _dispatch(asset_id: int, protocol: str) -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id="sess-1",
        connector_id="conn-1",
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id=str(asset_id),
        account_id="app_user",
        protocol=protocol,
    )


def _resolver(
    session_factory: async_sessionmaker[AsyncSession], scanner: FakeScanner
) -> AssetVaultSessionConnectionResolver:
    return AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({"sec_db": "db-pass"}),
        host_keys=HostKeyTrustStore(session_factory),
        scanner=scanner,
    )


@pytest.mark.asyncio
async def test_resolver_returns_postgresql_spec(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scanner = FakeScanner()
    asset = await _seed_database_asset(
        session_factory,
        protocol="postgresql",
        address="10.0.0.20",
        port=5432,
        namespace="appdb",
    )
    spec = await _resolver(session_factory, scanner).resolve(_dispatch(asset.id, "postgresql"))
    assert spec.mode is ConnectorSessionMode.DB_POSTGRESQL
    assert spec.postgres_target is not None
    assert spec.postgres_target.host == "10.0.0.20"
    assert spec.postgres_target.database == "appdb"
    assert spec.postgres_target.username == "app_user"
    assert spec.postgres_target.require_tls is False
    assert spec.postgres_credential is not None
    assert spec.postgres_credential.password == "db-pass"
    assert "db-pass" not in repr(spec.postgres_credential)
    assert scanner.calls == 0


@pytest.mark.asyncio
async def test_resolver_returns_mysql_spec_with_tls_when_ca_present(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scanner = FakeScanner()
    asset = await _seed_database_asset(
        session_factory,
        protocol="mysql",
        address="10.0.0.30",
        port=3306,
        server_ca="-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n",
    )
    spec = await _resolver(session_factory, scanner).resolve(_dispatch(asset.id, "mysql"))
    assert spec.mode is ConnectorSessionMode.DB_MYSQL
    assert spec.mysql_target is not None
    assert spec.mysql_target.host == "10.0.0.30"
    assert spec.mysql_target.database == "mysql"
    assert spec.mysql_target.require_tls is True
    assert spec.mysql_credential is not None
    assert spec.mysql_credential.password == "db-pass"
    assert scanner.calls == 0


@pytest.mark.asyncio
async def test_resolver_maps_mariadb_to_mysql_channel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scanner = FakeScanner()
    asset = await _seed_database_asset(
        session_factory, protocol="mariadb", address="10.0.0.40", port=3306
    )
    spec = await _resolver(session_factory, scanner).resolve(_dispatch(asset.id, "mariadb"))
    assert spec.mode is ConnectorSessionMode.DB_MYSQL
    assert spec.mysql_target is not None
