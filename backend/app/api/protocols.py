"""#t66 协议目录与 Platform 协议约束 API。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.protocol_schemas import (
    AssetTypeProtocolsResponse,
    PlatformProtocolListResponse,
    PlatformProtocolResponse,
    ProtocolListResponse,
    ProtocolResponse,
)
from app.core.database import get_read_db
from app.core.deps import require_permission
from app.models.asset import Platform
from app.models.protocol import ProtocolModel
from app.protocols.catalog import PROTOCOL_CATALOG, protocols_for_asset_type
from app.protocols.repository import ensure_builtin_protocols
from app.protocols.validation import list_platform_protocol_rows, load_json_list

router = APIRouter(tags=["协议"])


def _protocol_response(row: ProtocolModel) -> ProtocolResponse:
    return ProtocolResponse(
        id=row.id,
        name=row.name,
        category=row.category,
        default_port=row.default_port,
        asset_types=load_json_list(row.asset_types_json),
        credential_types=load_json_list(row.credential_types_json),
        driver_module=row.driver_module,
        is_builtin=row.is_builtin,
    )


def _catalog_response(definition: Any) -> ProtocolResponse:
    return ProtocolResponse(
        id=definition.id,
        name=definition.name,
        category=definition.category,
        default_port=definition.default_port,
        asset_types=list(definition.asset_types),
        credential_types=list(definition.credential_types),
        driver_module=definition.driver_module,
        is_builtin=True,
    )


@router.get("/protocols/", response_model=ProtocolListResponse)
async def list_protocols(
    db: AsyncSession = Depends(get_read_db),
    _user: dict[str, Any] = Depends(require_permission("assets:read")),
) -> ProtocolListResponse:
    await ensure_builtin_protocols(db)
    result = await db.execute(select(ProtocolModel).order_by(ProtocolModel.id.asc()))
    rows = list(result.scalars().all())
    items = [_protocol_response(row) for row in rows]
    return ProtocolListResponse(items=items, total=len(items))


@router.get("/protocols/by-asset-type/{asset_type}", response_model=AssetTypeProtocolsResponse)
async def list_protocols_for_asset_type(
    asset_type: str,
    db: AsyncSession = Depends(get_read_db),
    _user: dict[str, Any] = Depends(require_permission("assets:read")),
) -> AssetTypeProtocolsResponse:
    await ensure_builtin_protocols(db)
    items = [_catalog_response(item) for item in protocols_for_asset_type(asset_type)]
    if not items:
        raise HTTPException(status_code=404, detail="ASSET_TYPE_NOT_FOUND")
    return AssetTypeProtocolsResponse(asset_type=asset_type, items=items, total=len(items))  # type: ignore[arg-type]


@router.get(
    "/assets/platforms/{platform_id}/protocols",
    response_model=PlatformProtocolListResponse,
)
async def list_platform_protocols(
    platform_id: int,
    db: AsyncSession = Depends(get_read_db),
    _user: dict[str, Any] = Depends(require_permission("assets:read")),
) -> PlatformProtocolListResponse:
    await ensure_builtin_protocols(db)
    platform = await db.get(Platform, platform_id)
    if platform is None:
        raise HTTPException(status_code=404, detail="PLATFORM_NOT_FOUND")

    rows = await list_platform_protocol_rows(db, platform_id)
    if rows:
        items = [
            PlatformProtocolResponse(
                protocol_id=protocol.id,
                name=protocol.name,
                category=protocol.category,
                port=link.port or protocol.default_port,
                credential_types=load_json_list(protocol.credential_types_json),
                driver_module=protocol.driver_module,
                is_primary=link.is_primary,
                settings=_load_settings(link.settings_json),
            )
            for link, protocol in rows
        ]
    else:
        items = []
        for index, protocol_id in enumerate(load_json_list(platform.protocols)):
            definition = next((item for item in PROTOCOL_CATALOG if item.id == protocol_id), None)
            if definition is None:
                continue
            items.append(
                PlatformProtocolResponse(
                    protocol_id=definition.id,
                    name=definition.name,
                    category=definition.category,
                    port=definition.default_port,
                    credential_types=list(definition.credential_types),
                    driver_module=definition.driver_module,
                    is_primary=index == 0,
                    settings={},
                )
            )

    return PlatformProtocolListResponse(
        platform_id=platform_id,
        asset_type=platform.asset_type,
        items=items,
        total=len(items),
    )


def _load_settings(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
