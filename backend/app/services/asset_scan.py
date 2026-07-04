"""Asset scan automation worker handler."""
from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.asset import Asset
from app.services.automation_worker import JsonValue


@dataclass(frozen=True)
class AssetScanTarget:
    id: int
    tenant_id: str
    name: str
    address: str
    port: int
    platform_id: int


class AssetScanner(Protocol):
    def scan(
        self,
        *,
        target: AssetScanTarget,
        scan_profile: str,
        requested_by: str,
    ) -> Awaitable[None]: ...


class AssetScanWorkerHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        scanner: AssetScanner,
    ) -> None:
        self._session_factory = session_factory
        self._scanner = scanner

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        del message_id
        asset_id = _payload_int(payload, "asset_id")
        scan_profile = _payload_str(payload, "scan_profile")

        async with self._session_factory() as session:
            asset = await _get_active_asset(session, tenant_id=tenant_id, asset_id=asset_id)
            if asset is None:
                raise ValueError("ASSET_NOT_FOUND")
            target = AssetScanTarget(
                id=asset.id,
                tenant_id=asset.tenant_id,
                name=asset.name,
                address=asset.address,
                port=asset.port,
                platform_id=asset.platform_id,
            )

        await self._scanner.scan(
            target=target,
            scan_profile=scan_profile,
            requested_by=requested_by,
        )


async def _get_active_asset(
    session: AsyncSession,
    *,
    tenant_id: str,
    asset_id: int,
) -> Asset | None:
    result = await session.execute(
        select(Asset)
        .where(Asset.id == asset_id)
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
    )
    return result.scalar_one_or_none()


def _payload_int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_str(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value
