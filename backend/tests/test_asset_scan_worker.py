"""Phase 4 asset scan worker handler tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.asset import Asset, Platform
from app.services.asset_scan import AssetScanTarget, AssetScanWorkerHandler


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class RecordingAssetScanner:
    def __init__(self) -> None:
        self.calls: list[tuple[AssetScanTarget, str, str]] = []

    async def scan(self, *, target: AssetScanTarget, scan_profile: str, requested_by: str) -> None:
        self.calls.append((target, scan_profile, requested_by))


async def seed_assets(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                tenant_id="tenant-a",
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                port=22,
                username="deploy",
                credential="legacy-password",
                is_active=True,
            )
        )
        session.add(
            Asset(
                id=2,
                tenant_id="tenant-b",
                name="other-tenant",
                address="203.0.113.11",
                platform_id=1,
                port=22,
                is_active=True,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_asset_scan_handler_scans_current_tenant_asset_without_credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_assets(session_factory)
    scanner = RecordingAssetScanner()
    handler = AssetScanWorkerHandler(session_factory=session_factory, scanner=scanner)

    await handler(
        tenant_id="tenant-a",
        requested_by="user-1",
        payload={"asset_id": 1, "scan_profile": "ssh-baseline"},
        message_id="1700000000000-0",
    )

    assert len(scanner.calls) == 1
    target, scan_profile, requested_by = scanner.calls[0]
    assert target == AssetScanTarget(
        id=1,
        tenant_id="tenant-a",
        name="prod-linux",
        address="203.0.113.10",
        port=22,
        platform_id=1,
    )
    assert not hasattr(target, "credential")
    assert scan_profile == "ssh-baseline"
    assert requested_by == "user-1"


@pytest.mark.asyncio
async def test_asset_scan_handler_rejects_cross_tenant_asset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_assets(session_factory)
    scanner = RecordingAssetScanner()
    handler = AssetScanWorkerHandler(session_factory=session_factory, scanner=scanner)

    with pytest.raises(ValueError, match="ASSET_NOT_FOUND"):
        await handler(
            tenant_id="tenant-a",
            requested_by="user-1",
            payload={"asset_id": 2, "scan_profile": "ssh-baseline"},
            message_id="1700000000000-0",
        )

    assert scanner.calls == []
