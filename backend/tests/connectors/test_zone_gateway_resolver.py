"""#t67 网域网关 Resolver：空成员 fail-closed；直连仍可用；经网关写入 jump。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.asset_vault_resolver import (
    ZONE_GATEWAY_UNAVAILABLE,
    AssetVaultSessionConnectionResolver,
    MappingSecretUnwrapper,
)
from app.connectors.host_key_trust import CONNECT_DENIED_COPY, HostKeyTrustStore
from app.connectors.ssh_channel import SshChannelError
from app.connectors.ssh_hostkey import HostKeyScan
from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.zone import Zone, ZoneGateway


@dataclass
class FakeScanner:
    public_key: str
    fingerprint: str = "SHA256:fake"
    calls: int = 0

    async def scan(self, host: str, port: int) -> HostKeyScan:
        self.calls += 1
        return HostKeyScan(
            host=host,
            port=port,
            key_type="ssh-ed25519",
            public_key=self.public_key,
            fingerprint=self.fingerprint,
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


async def _seed_pair(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    with_zone_members: bool,
) -> tuple[Asset, Asset, str]:
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey"
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", asset_type="host", protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        gateway = Asset(
            name="jump",
            address="10.0.0.2",
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="host",
            port=22,
            username="jumpuser",
            is_active=True,
        )
        target = Asset(
            name="prod",
            address="10.0.0.10",
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="host",
            port=22,
            username="root",
            is_active=True,
        )
        session.add_all([gateway, target])
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=gateway.id,
                username="jumpuser",
                protocol="ssh",
                secret_id="sec_jump",
                status="active",
            )
        )
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=target.id,
                username="root",
                protocol="ssh",
                secret_id="sec_root",
                status="active",
            )
        )
        zone = Zone(tenant_id="tenant-a", name="z1")
        session.add(zone)
        await session.flush()
        target.zone_id = zone.id
        if with_zone_members:
            session.add(
                ZoneGateway(tenant_id="tenant-a", zone_id=zone.id, asset_id=gateway.id)
            )
        await session.commit()
        await session.refresh(target)
        await session.refresh(gateway)
        return target, gateway, key


async def _approve(
    store: HostKeyTrustStore,
    *,
    asset_id: int,
    public_key: str,
) -> None:
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(asset_id),
        public_key=public_key,
        fingerprint="SHA256:fake",
        host="10.0.0.10",
        port=22,
    )


def _resolver(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    public_key: str,
) -> tuple[AssetVaultSessionConnectionResolver, HostKeyTrustStore, FakeScanner]:
    store = HostKeyTrustStore(session_factory)
    scanner = FakeScanner(public_key=public_key)
    resolver = AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({"sec_root": "password", "sec_jump": "jump-pass"}),
        host_keys=store,
        scanner=scanner,
    )
    return resolver, store, scanner


@pytest.mark.asyncio
async def test_empty_zone_members_fail_closed_cannot_connect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target, _gateway, key = await _seed_pair(session_factory, with_zone_members=False)
    resolver, store, _scanner = _resolver(session_factory, public_key=key)
    await _approve(store, asset_id=target.id, public_key=key)

    with pytest.raises(SshChannelError) as excinfo:
        await resolver.resolve(
            ConnectorDispatchRequest(
                session_id="s1",
                connector_id="c1",
                tenant_id="tenant-a",
                subject_id="u1",
                asset_id=str(target.id),
                account_id="root",
                protocol="ssh",
            )
        )
    assert excinfo.value.code == ZONE_GATEWAY_UNAVAILABLE
    assert CONNECT_DENIED_COPY == "无法连接"
    assert "没有权限" not in str(excinfo.value)
    assert "网关" not in CONNECT_DENIED_COPY
    assert "跳板" not in CONNECT_DENIED_COPY


@pytest.mark.asyncio
async def test_zone_with_usable_gateway_sets_jump(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target, gateway, key = await _seed_pair(session_factory, with_zone_members=True)
    resolver, store, scanner = _resolver(session_factory, public_key=key)
    await _approve(store, asset_id=target.id, public_key=key)
    await _approve(store, asset_id=gateway.id, public_key=key)

    spec = await resolver.resolve(
        ConnectorDispatchRequest(
            session_id="s1",
            connector_id="c1",
            tenant_id="tenant-a",
            subject_id="u1",
            asset_id=str(target.id),
            account_id="root",
            protocol="ssh",
        )
    )
    assert spec.jump_target is not None
    assert spec.jump_target.host == "10.0.0.2"
    assert spec.jump_target.username == "jumpuser"
    assert spec.jump_credential is not None
    assert spec.target is not None
    assert spec.target.host == "10.0.0.10"
    assert scanner.calls >= 1  # gateway probe


@pytest.mark.asyncio
async def test_direct_ssh_still_works_without_zone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDirect"
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", asset_type="host", protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="direct",
            address="10.0.0.10",
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="host",
            port=22,
            username="root",
            is_active=True,
            zone_id=None,
        )
        session.add(asset)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="root",
                protocol="ssh",
                secret_id="sec_root",
                status="active",
            )
        )
        await session.commit()
        await session.refresh(asset)

    resolver, store, _scanner = _resolver(session_factory, public_key=key)
    await _approve(store, asset_id=asset.id, public_key=key)
    spec = await resolver.resolve(
        ConnectorDispatchRequest(
            session_id="s1",
            connector_id="c1",
            tenant_id="tenant-a",
            subject_id="u1",
            asset_id=str(asset.id),
            account_id="root",
            protocol="ssh",
        )
    )
    assert spec.jump_target is None
    assert spec.jump_credential is None
    assert spec.target is not None
    assert spec.target.host == "10.0.0.10"
