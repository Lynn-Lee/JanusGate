"""资产 API 路由。"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, get_read_db
from app.core.deps import require_permission
from app.models.asset import Asset, Platform
from app.policy.asset_permission import connectable_asset_ids
from app.policy.asset_tree_ops import list_assets as list_scoped_assets
from app.policy.asset_tree_ops import list_nodes, list_permissions, nodes_by_id
from app.protocols.repository import ensure_builtin_protocols, sync_platform_protocols
from app.protocols.validation import ProtocolValidationError, validate_asset_protocol_binding
from app.schemas.asset import AssetCreate, AssetResponse, PlatformCreate, PlatformResponse
from app.services.asset import AssetService
from app.tenancy.scope import actor_scope_from_user

router = APIRouter(prefix="/assets", tags=["资产管理"])


@router.get("/", response_model=list[AssetResponse])
async def list_assets(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(require_permission("assets:read")),
) -> list[AssetResponse]:
    """使用面：只返回当前有效 connect 的资产。admin 不绕过。"""

    visible = await _visible_assets(db, user)
    sliced = visible[skip: skip + limit]
    return [_asset_response(asset) for asset in sliced]


@router.post("/", response_model=AssetResponse)
async def create_asset(
    data: AssetCreate, db: AsyncSession = Depends(get_db), _user: dict[str, Any] = Depends(require_permission("assets:write"))
) -> AssetResponse:
    await ensure_builtin_protocols(db)
    try:
        await validate_asset_protocol_binding(
            db, asset_type=data.asset_type, platform_id=data.platform_id
        )
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    asset = await AssetService.create_asset(db, data.model_dump())
    return _asset_response(asset)


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: int, db: AsyncSession = Depends(get_db), _user: dict[str, Any] = Depends(require_permission("assets:write"))
) -> dict[str, str]:
    deleted = await AssetService.delete_asset(db, asset_id)
    if not deleted:
        raise HTTPException(404, "资产不存在")
    return {"status": "ok"}


@router.post("/test-connection")
async def test_connection(
    asset_id: int | None = None,
    address: str | None = None,
    port: int = 22,
    db: AsyncSession = Depends(get_db),
    _user: dict[str, Any] = Depends(require_permission("assets:test")),
) -> dict[str, Any]:
    if asset_id is not None:
        asset = await AssetService.get_asset(db, asset_id)
        if not asset or not asset.is_active:
            raise HTTPException(404, "资产不存在")
        return await AssetService.test_connection(asset.address, asset.port)
    if not address or address not in settings.ASSET_TEST_CONNECTION_ALLOWLIST:
        raise HTTPException(400, "连接测试目标必须是已登记资产或显式 allowlist")
    return await AssetService.test_connection(address, port)


@router.post("/platforms", response_model=PlatformResponse)
async def create_platform(
    data: PlatformCreate, db: AsyncSession = Depends(get_db), _user: dict[str, Any] = Depends(require_permission("assets:write"))
) -> PlatformResponse:
    await ensure_builtin_protocols(db)
    payload = data.model_dump()
    if payload.get("category") and not payload.get("asset_type"):
        payload["asset_type"] = payload["category"]
    platform = Platform(**payload)
    db.add(platform)
    await db.commit()
    await db.refresh(platform)
    await sync_platform_protocols(db, platform)
    return _platform_response(platform)


@router.get("/platforms", response_model=list[PlatformResponse])
async def list_platforms(
    db: AsyncSession = Depends(get_read_db), _user: dict[str, Any] = Depends(require_permission("assets:read"))
) -> list[PlatformResponse]:
    result = await db.execute(select(Platform).order_by(Platform.id))
    platforms = result.scalars().all()
    return [
        _platform_response(p)
        for p in platforms
    ]


def _platform_response(platform: Platform) -> PlatformResponse:
    return PlatformResponse(
        id=platform.id,
        name=platform.name,
        category=platform.category,
        asset_type=getattr(platform, "asset_type", platform.category),
        protocols=platform.protocols,
        is_active=platform.is_active,
    )


def _asset_response(asset: Asset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        name=asset.name,
        address=asset.address,
        platform_id=asset.platform_id,
        asset_type=getattr(asset, "asset_type", None) or "host",
        port=asset.port,
        username=asset.username,
        is_active=asset.is_active,
        description=asset.description,
        created_at=asset.created_at.isoformat() if asset.created_at else "",
    )

@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(require_permission("assets:read")),
) -> AssetResponse:
    visible = await _visible_assets(db, user)
    asset = next((item for item in visible if item.id == asset_id), None)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    return _asset_response(asset)


async def _visible_assets(db: AsyncSession, user: dict[str, Any]) -> list[Asset]:
    actor_scope = actor_scope_from_user(user)
    assets = await list_scoped_assets(db, actor_scope)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    permissions = await list_permissions(db, actor_scope)
    allowed = connectable_asset_ids(
        subject_id=str(user["id"]),
        subject_group_ids=tuple(str(group_id) for group_id in user.get("group_ids", ())),
        tenant_id=actor_scope.tenant_id,
        assets=[(str(asset.id), asset.node_id) for asset in assets],
        permissions=permissions,
        nodes_by_id=nodes,
    )
    return [asset for asset in assets if str(asset.id) in allowed]
