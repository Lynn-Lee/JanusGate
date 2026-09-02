"""#t66 协议种子与 Platform 协议同步。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Platform
from app.models.protocol import PlatformProtocolModel, ProtocolModel
from app.protocols.catalog import PROTOCOL_CATALOG


def dump_json_list(values: list[str]) -> str:
    return json.dumps(values)


async def ensure_builtin_protocols(db: AsyncSession) -> list[ProtocolModel]:
    """幂等写入全局协议目录。"""
    result = await db.execute(select(ProtocolModel))
    existing = {row.id: row for row in result.scalars().all()}
    created: list[ProtocolModel] = []
    for definition in PROTOCOL_CATALOG:
        if definition.id in existing:
            continue
        row = ProtocolModel(
            id=definition.id,
            name=definition.name,
            category=definition.category,
            default_port=definition.default_port,
            asset_types_json=dump_json_list(list(definition.asset_types)),
            credential_types_json=dump_json_list(list(definition.credential_types)),
            driver_module=definition.driver_module,
            is_builtin=True,
        )
        db.add(row)
        created.append(row)
    if created:
        await db.commit()
        for row in created:
            await db.refresh(row)
    return created


async def sync_platform_protocols(db: AsyncSession, platform: Platform) -> None:
    """从 Platform.protocols JSON 同步到 platform_protocols 关联表。"""
    try:
        parsed = json.loads(platform.protocols or "[]")
    except json.JSONDecodeError:
        parsed = []
    protocol_ids = [str(item) for item in parsed if item]
    if not protocol_ids:
        return

    result = await db.execute(
        select(PlatformProtocolModel).where(PlatformProtocolModel.platform_id == platform.id)
    )
    existing = {row.protocol_id: row for row in result.scalars().all()}
    for index, protocol_id in enumerate(protocol_ids):
        if protocol_id in existing:
            continue
        definition = next((item for item in PROTOCOL_CATALOG if item.id == protocol_id), None)
        db.add(
            PlatformProtocolModel(
                platform_id=platform.id,
                protocol_id=protocol_id,
                port=definition.default_port if definition else None,
                is_primary=index == 0,
            )
        )
    await db.commit()
