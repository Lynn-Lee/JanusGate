"""#t67 网域随机网关选取与 resolver ProxyJump 接线测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.asset_vault_resolver import (
    AssetVaultSessionConnectionResolver,
    MappingSecretUnwrapper,
)
from app.connectors.host_key_trust import HostKeyTrustStore
from app.connectors.ssh_channel import SshChannelError
from app.connectors.ssh_hostkey import HostKeyScan
from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.zone import ZoneGatewayModel, ZoneModel
from app.tenancy.scope import ActorScope
from app.zones import service as zone_service


@dataclass
class FakeScanner:
    keys: dict[tuple[str, int], str]

    async def scan(self, host: str, port: int) -> HostKeyScan:
        public_key = self.keys.get((host, port), "ssh-ed25519 AAAAFAKE")
        return HostKeyScan(
            host=host,
            port=port,
            key_type="ssh-ed25519",
            public_key=public_key,
            fingerprint="SHA256:fake",
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


async def _seed_zone_topology(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Asset, Asset, ZoneModel]:
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", protocols='["ssh"]')
        session.add(platform)
        await session.flush()
        zone = ZoneModel(id="zone_dmz", tenant_id="tenant-a", name="DMZ", is_active=True)
        session.add(zone)
        await session.flush()
        gateway = Asset(
            name="gateway",
            address="203.0.113.1",
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=22,
            is_active=True,
        )
        target = Asset(
            name="inner",
            address="10.0.0.8",
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=22,
            zone_id=zone.id,
            is_active=True,
        )
        session.add(gateway)
        session.add(target)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=gateway.id,
                username="gw",
                protocol="ssh",
                secret_id="sec_gw",
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
        session.add(
            ZoneGatewayModel(
                tenant_id="tenant-a",
                zone_id=zone.id,
                gateway_asset_id=gateway.id,
                is_active=True,
            )
        )
        await session.commit()
        await session.refresh(target)
        await session.refresh(gateway)
        return target, gateway, zone


@pytest.mark.asyncio
async def test_pick_random_active_gateway(session_factory: async_sessionmaker[AsyncSession]) -> None:
    _, gateway, zone = await _seed_zone_topology(session_factory)
    scope = ActorScope(user_id="u1", tenant_id="tenant-a", permissions=("admin",))
    async with session_factory() as session:
        picked = await zone_service.pick_random_active_gateway(session, scope, zone.id)
    assert picked is not None
    _, picked_asset = picked
    assert picked_asset.id == gateway.id


@pytest.mark.asyncio
async def test_resolver_builds_proxy_jump_when_asset_in_zone(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, gateway, _ = await _seed_zone_topology(session_factory)

    async def _reachable_probe(address: str, port: int, timeout: float = 5.0) -> dict[str, str | bool]:
        return {"reachable": True, "error": ""}

    monkeypatch.setattr(
        "app.connectors.asset_vault_resolver.AssetService.probe_registered_host",
        _reachable_probe,
    )
    gw_key = "ssh-ed25519 AAAAGWKEY"
    inner_key = "ssh-ed25519 AAAAInner"
    scanner = FakeScanner(
        keys={
            (gateway.address, gateway.port): gw_key,
            (target.address, target.port): inner_key,
        }
    )
    store = HostKeyTrustStore(session_factory)
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(gateway.id),
        public_key=gw_key,
        fingerprint="SHA256:gw",
        host=gateway.address,
        port=gateway.port,
    )
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(target.id),
        public_key=inner_key,
        fingerprint="SHA256:inner",
        host=target.address,
        port=target.port,
    )
    resolver = AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({"sec_gw": "gw-pass", "sec_root": "root-pass"}),
        host_keys=store,
        scanner=scanner,
    )

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

    assert spec.proxy_jump is not None
    assert spec.proxy_jump.target.host == gateway.address
    assert spec.proxy_jump.target.username == "gw"
    # P0#16：网关凭据同样经 Vault unwrap 进入内存，不得明文旁路。
    assert spec.proxy_jump.credential.password == "gw-pass"
    assert spec.target.host == target.address
    assert spec.credential.password == "root-pass"


@pytest.mark.asyncio
async def test_resolver_fails_when_no_gateway_available(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    target, _, zone = await _seed_zone_topology(session_factory)
    async with session_factory() as session:
        rows = await zone_service.list_zone_gateways(
            session, ActorScope(user_id="u1", tenant_id="tenant-a", permissions=("admin",)),
            zone.id,
        )
        for row in rows:
            row.is_active = False
        await session.commit()

    store = HostKeyTrustStore(session_factory)
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(target.id),
        public_key="ssh-ed25519 AAAAInner",
        fingerprint="SHA256:inner",
    )
    resolver = AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({"sec_root": "root-pass"}),
        host_keys=store,
        scanner=FakeScanner(keys={}),
    )

    with pytest.raises(SshChannelError, match="ZONE_GATEWAY_UNAVAILABLE"):
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
