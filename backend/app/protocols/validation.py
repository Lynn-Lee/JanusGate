"""#t66 协议与 Platform 协议约束校验。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Platform
from app.models.protocol import PlatformProtocolModel, ProtocolModel
from app.protocols.catalog import ALL_ASSET_TYPES, validate_protocol_for_asset


class ProtocolValidationError(ValueError):
    pass


def load_json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]


async def platform_allows_protocol(
    db: AsyncSession, *, platform_id: int, protocol_id: str
) -> bool:
    result = await db.execute(
        select(PlatformProtocolModel.protocol_id).where(
            PlatformProtocolModel.platform_id == platform_id,
            PlatformProtocolModel.protocol_id == protocol_id,
        )
    )
    if result.scalar_one_or_none() is not None:
        return True

    platform = await db.get(Platform, platform_id)
    if platform is None:
        return False
    return protocol_id in load_json_list(platform.protocols)


async def validate_asset_protocol_binding(
    db: AsyncSession,
    *,
    asset_type: str,
    platform_id: int,
    protocol_id: str | None = None,
) -> None:
    if asset_type not in ALL_ASSET_TYPES:
        raise ProtocolValidationError("ASSET_TYPE_INVALID")

    platform = await db.get(Platform, platform_id)
    if platform is None:
        raise ProtocolValidationError("PLATFORM_NOT_FOUND")
    if platform.asset_type and platform.asset_type != asset_type:
        raise ProtocolValidationError("PLATFORM_ASSET_TYPE_MISMATCH")

    if protocol_id is None:
        return
    if not validate_protocol_for_asset(asset_type, protocol_id):
        raise ProtocolValidationError("PROTOCOL_ASSET_TYPE_MISMATCH")
    if not await platform_allows_protocol(db, platform_id=platform_id, protocol_id=protocol_id):
        raise ProtocolValidationError("PLATFORM_PROTOCOL_NOT_ALLOWED")


async def list_platform_protocol_rows(
    db: AsyncSession, platform_id: int
) -> list[tuple[PlatformProtocolModel, ProtocolModel]]:
    result = await db.execute(
        select(PlatformProtocolModel, ProtocolModel)
        .join(ProtocolModel, ProtocolModel.id == PlatformProtocolModel.protocol_id)
        .where(PlatformProtocolModel.platform_id == platform_id)
        .order_by(PlatformProtocolModel.is_primary.desc(), PlatformProtocolModel.protocol_id.asc())
    )
    return list(result.all())
