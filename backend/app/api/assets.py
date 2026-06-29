"""资产 API 路由。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import current_user
from app.models.asset import Platform
from app.schemas.asset import AssetCreate, AssetResponse, PlatformCreate, PlatformResponse
from app.services.asset import AssetService

router = APIRouter(prefix="/assets", tags=["资产管理"])


@router.get("/", response_model=list[AssetResponse])
async def list_assets(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(current_user),
):
    assets = await AssetService.list_assets(db, skip, limit)
    return [
        AssetResponse(
            id=a.id, name=a.name, address=a.address, platform_id=a.platform_id,
            port=a.port, username=a.username, is_active=a.is_active,
            description=a.description, created_at=a.created_at.isoformat() if a.created_at else "",
        )
        for a in assets
    ]


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: int, db: AsyncSession = Depends(get_db), _user: dict = Depends(current_user)
):
    asset = await AssetService.get_asset(db, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    return AssetResponse(
        id=asset.id, name=asset.name, address=asset.address, platform_id=asset.platform_id,
        port=asset.port, username=asset.username, is_active=asset.is_active,
        description=asset.description, created_at=asset.created_at.isoformat() if asset.created_at else "",
    )


@router.post("/", response_model=AssetResponse)
async def create_asset(
    data: AssetCreate, db: AsyncSession = Depends(get_db), _user: dict = Depends(current_user)
):
    asset = await AssetService.create_asset(db, data.model_dump())
    return AssetResponse(
        id=asset.id, name=asset.name, address=asset.address, platform_id=asset.platform_id,
        port=asset.port, username=asset.username, is_active=asset.is_active,
        description=asset.description, created_at=asset.created_at.isoformat() if asset.created_at else "",
    )


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: int, db: AsyncSession = Depends(get_db), _user: dict = Depends(current_user)
):
    deleted = await AssetService.delete_asset(db, asset_id)
    if not deleted:
        raise HTTPException(404, "资产不存在")
    return {"status": "ok"}


@router.post("/test-connection")
async def test_connection(address: str, port: int = 22, _user: dict = Depends(current_user)):
    return await AssetService.test_connection(address, port)


@router.post("/platforms", response_model=PlatformResponse)
async def create_platform(
    data: PlatformCreate, db: AsyncSession = Depends(get_db), _user: dict = Depends(current_user)
):
    platform = Platform(**data.model_dump())
    db.add(platform)
    await db.commit()
    await db.refresh(platform)
    return PlatformResponse(
        id=platform.id, name=platform.name, category=platform.category,
        protocols=platform.protocols, is_active=platform.is_active,
    )


@router.get("/platforms", response_model=list[PlatformResponse])
async def list_platforms(
    db: AsyncSession = Depends(get_db), _user: dict = Depends(current_user)
):
    result = await db.execute(select(Platform).order_by(Platform.id))
    platforms = result.scalars().all()
    return [
        PlatformResponse(
            id=p.id, name=p.name, category=p.category,
            protocols=p.protocols, is_active=p.is_active,
        )
        for p in platforms
    ]
