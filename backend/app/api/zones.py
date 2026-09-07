"""#t67 网域 CRUD（系统设置风格，权限同 overlay ACL）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.zone_schemas import ZoneCreate, ZoneListResponse, ZoneResponse, ZoneUpdate
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.asset import Asset
from app.models.zone import Zone, ZoneGateway
from app.protocols.catalog import PROTOCOL_CATALOG
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(prefix="/zones", tags=["网域"])

HOST_CLASS_ASSET_TYPES: frozenset[str] = frozenset(
    asset_type
    for definition in PROTOCOL_CATALOG
    if definition.id == "ssh"
    for asset_type in definition.asset_types
)


def _require_zone_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _require_zone_list_permission(user: dict[str, Any]) -> None:
    """设置页 ACL 读权限，或资产读写（所属网域下拉）。"""

    permissions = user.get("permissions", [])
    allowed = {"admin", "acl:read", "assets:read", "assets:write"}
    if allowed.intersection(permissions):
        return
    raise HTTPException(status_code=403, detail="缺少权限: acl:read")


@router.get("/", response_model=ZoneListResponse)
async def list_zones(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneListResponse:
    _require_zone_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(Zone, actor_scope).order_by(Zone.id.asc()))
    zones = list(result.scalars().all())
    members = await _members_by_zone(db, actor_scope, [zone.id for zone in zones])
    items = [_zone_response(zone, members.get(zone.id, [])) for zone in zones]
    return ZoneListResponse(items=items, total=len(items))


@router.post("/", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
async def create_zone(
    data: ZoneCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneResponse:
    _require_zone_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    gateway_ids = await _validate_gateway_assets(db, actor_scope, data.gateway_asset_ids)
    zone = Zone(tenant_id=actor_scope.tenant_id, name=name)
    db.add(zone)
    await db.flush()
    await _replace_gateways(db, actor_scope, zone.id, gateway_ids)
    await db.commit()
    await db.refresh(zone)
    return _zone_response(zone, gateway_ids)



@router.get("/gateway-candidates/", response_model=list[dict[str, object]])
async def list_gateway_candidates(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, object]]:
    """系统设置网关多选：租户内 host-class 资产。"""

    _require_zone_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(Asset, actor_scope)
        .where(Asset.asset_type.in_(tuple(HOST_CLASS_ASSET_TYPES)))
        .order_by(Asset.id.asc())
    )
    return [
        {
            "id": asset.id,
            "name": asset.name,
            "address": asset.address,
            "asset_type": getattr(asset, "asset_type", None) or "host",
            "is_active": bool(asset.is_active),
        }
        for asset in result.scalars().all()
    ]


@router.get("/{zone_id}", response_model=ZoneResponse)
async def get_zone(
    zone_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneResponse:
    _require_zone_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    zone = await _get_scoped_zone(db, actor_scope, zone_id)
    members = await _members_by_zone(db, actor_scope, [zone.id])
    return _zone_response(zone, members.get(zone.id, []))


@router.patch("/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: int,
    data: ZoneUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneResponse:
    _require_zone_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    zone = await _get_scoped_zone(db, actor_scope, zone_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        zone.name = name
    members = await _members_by_zone(db, actor_scope, [zone.id])
    gateway_ids = members.get(zone.id, [])
    if "gateway_asset_ids" in payload:
        gateway_ids = await _validate_gateway_assets(
            db, actor_scope, list(payload["gateway_asset_ids"] or [])
        )
        await _replace_gateways(db, actor_scope, zone.id, gateway_ids)
    await db.commit()
    await db.refresh(zone)
    return _zone_response(zone, gateway_ids)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_zone(
    zone_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    """删除网域并清空资产所属网域；有成员也不阻断。"""

    _require_zone_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    zone = await _get_scoped_zone(db, actor_scope, zone_id)
    await db.execute(
        update(Asset)
        .where(Asset.tenant_id == actor_scope.tenant_id)
        .where(Asset.zone_id == zone.id)
        .values(zone_id=None)
    )
    await db.execute(delete(ZoneGateway).where(ZoneGateway.zone_id == zone.id))
    await db.delete(zone)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_scoped_zone(db: AsyncSession, actor_scope: ActorScope, zone_id: int) -> Zone:
    result = await db.execute(scoped_select(Zone, actor_scope).where(Zone.id == zone_id))
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=404, detail="ZONE_NOT_FOUND")
    return zone


async def _members_by_zone(
    db: AsyncSession, actor_scope: ActorScope, zone_ids: list[int]
) -> dict[int, list[int]]:
    if not zone_ids:
        return {}
    result = await db.execute(
        scoped_select(ZoneGateway, actor_scope)
        .where(ZoneGateway.zone_id.in_(zone_ids))
        .order_by(ZoneGateway.id.asc())
    )
    mapped: dict[int, list[int]] = {zone_id: [] for zone_id in zone_ids}
    for row in result.scalars().all():
        mapped.setdefault(row.zone_id, []).append(row.asset_id)
    return mapped


async def _validate_gateway_assets(
    db: AsyncSession, actor_scope: ActorScope, asset_ids: list[int]
) -> list[int]:
    unique_ids: list[int] = []
    seen: set[int] = set()
    for asset_id in asset_ids:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        unique_ids.append(asset_id)
    if not unique_ids:
        return []
    result = await db.execute(
        scoped_select(Asset, actor_scope).where(Asset.id.in_(unique_ids))
    )
    assets = {asset.id: asset for asset in result.scalars().all()}
    ordered: list[int] = []
    for asset_id in unique_ids:
        asset = assets.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=400, detail="网关资产不存在")
        asset_type = getattr(asset, "asset_type", None) or "host"
        if asset_type not in HOST_CLASS_ASSET_TYPES:
            raise HTTPException(status_code=400, detail="网关成员必须是 host-class 资产")
        ordered.append(asset_id)
    return ordered


async def _replace_gateways(
    db: AsyncSession, actor_scope: ActorScope, zone_id: int, asset_ids: list[int]
) -> None:
    await db.execute(delete(ZoneGateway).where(ZoneGateway.zone_id == zone_id))
    for asset_id in asset_ids:
        db.add(
            ZoneGateway(
                tenant_id=actor_scope.tenant_id,
                zone_id=zone_id,
                asset_id=asset_id,
            )
        )


def _zone_response(zone: Zone, gateway_asset_ids: list[int]) -> ZoneResponse:
    return ZoneResponse(
        id=zone.id,
        name=zone.name,
        gateway_count=len(gateway_asset_ids),
        gateway_asset_ids=list(gateway_asset_ids),
    )
