"""#t67 网域与网关中转管理 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.zone_schemas import (
    ZoneCreate,
    ZoneGatewayCreate,
    ZoneGatewayListResponse,
    ZoneGatewayProbeResponse,
    ZoneGatewayResponse,
    ZoneListResponse,
    ZoneResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.zone import ZoneGatewayModel, ZoneModel
from app.tenancy.scope import actor_scope_from_user
from app.zones import service as zone_service

router = APIRouter(prefix="/zones", tags=["网域与网关"])


def _require_assets_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _zone_response(zone: ZoneModel, *, gateway_count: int = 0) -> ZoneResponse:
    return ZoneResponse(
        id=zone.id,
        tenant_id=zone.tenant_id,
        name=zone.name,
        is_active=zone.is_active,
        gateway_count=gateway_count,
    )


def _gateway_response(row: ZoneGatewayModel) -> ZoneGatewayResponse:
    last_probe = row.last_probe_at.isoformat() if row.last_probe_at is not None else None
    return ZoneGatewayResponse(
        id=row.id,
        zone_id=row.zone_id,
        gateway_asset_id=row.gateway_asset_id,
        gateway_account_id=row.gateway_account_id,
        is_active=row.is_active,
        last_probe_at=last_probe,
        probe_status=row.probe_status,
        probe_error=row.probe_error,
    )


@router.get("/", response_model=ZoneListResponse)
async def list_zones(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneListResponse:
    _require_assets_permission(user, "assets:read")
    scope = actor_scope_from_user(user)
    zones = await zone_service.list_zones(db, scope)
    items: list[ZoneResponse] = []
    for zone in zones:
        gateways = await zone_service.list_zone_gateways(db, scope, zone.id)
        items.append(_zone_response(zone, gateway_count=len(gateways)))
    return ZoneListResponse(items=items)


@router.post("/", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
async def create_zone(
    body: ZoneCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneResponse:
    _require_assets_permission(user, "assets:write")
    scope = actor_scope_from_user(user)
    zone = await zone_service.create_zone(db, scope, name=body.name, is_active=body.is_active)
    return _zone_response(zone)


@router.get("/{zone_id}", response_model=ZoneResponse)
async def get_zone(
    zone_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneResponse:
    _require_assets_permission(user, "assets:read")
    scope = actor_scope_from_user(user)
    zone = await zone_service.get_zone(db, scope, zone_id)
    if zone is None:
        raise HTTPException(status_code=404, detail="ZONE_NOT_FOUND")
    gateways = await zone_service.list_zone_gateways(db, scope, zone_id)
    return _zone_response(zone, gateway_count=len(gateways))


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    zone_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> None:
    _require_assets_permission(user, "assets:write")
    scope = actor_scope_from_user(user)
    try:
        deleted = await zone_service.delete_zone(db, scope, zone_id)
    except ValueError as exc:
        if str(exc) == "ZONE_HAS_ASSETS":
            raise HTTPException(status_code=409, detail="ZONE_HAS_ASSETS") from exc
        raise
    if not deleted:
        raise HTTPException(status_code=404, detail="ZONE_NOT_FOUND")


@router.get("/{zone_id}/gateways", response_model=ZoneGatewayListResponse)
async def list_zone_gateways(
    zone_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneGatewayListResponse:
    _require_assets_permission(user, "assets:read")
    scope = actor_scope_from_user(user)
    if await zone_service.get_zone(db, scope, zone_id) is None:
        raise HTTPException(status_code=404, detail="ZONE_NOT_FOUND")
    rows = await zone_service.list_zone_gateways(db, scope, zone_id)
    return ZoneGatewayListResponse(items=[_gateway_response(row) for row in rows])


@router.post(
    "/{zone_id}/gateways",
    response_model=ZoneGatewayResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_zone_gateway(
    zone_id: str,
    body: ZoneGatewayCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneGatewayResponse:
    _require_assets_permission(user, "assets:write")
    scope = actor_scope_from_user(user)
    try:
        row = await zone_service.add_zone_gateway(
            db,
            scope,
            zone_id=zone_id,
            gateway_asset_id=body.gateway_asset_id,
            gateway_account_id=body.gateway_account_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "GATEWAY_ALREADY_REGISTERED":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return _gateway_response(row)


@router.delete("/{zone_id}/gateways/{gateway_asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_zone_gateway(
    zone_id: str,
    gateway_asset_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> None:
    _require_assets_permission(user, "assets:write")
    scope = actor_scope_from_user(user)
    removed = await zone_service.remove_zone_gateway(
        db, scope, zone_id=zone_id, gateway_asset_id=gateway_asset_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="ZONE_GATEWAY_NOT_FOUND")


@router.post(
    "/{zone_id}/gateways/{gateway_asset_id}/probe",
    response_model=ZoneGatewayProbeResponse,
)
async def probe_zone_gateway(
    zone_id: str,
    gateway_asset_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ZoneGatewayProbeResponse:
    _require_assets_permission(user, "assets:test")
    scope = actor_scope_from_user(user)
    try:
        row = await zone_service.probe_gateway(
            db, scope, zone_id=zone_id, gateway_asset_id=gateway_asset_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    last_probe = row.last_probe_at.isoformat() if row.last_probe_at is not None else None
    return ZoneGatewayProbeResponse(
        gateway_asset_id=row.gateway_asset_id,
        probe_status=row.probe_status,
        probe_error=row.probe_error,
        last_probe_at=last_probe,
    )
