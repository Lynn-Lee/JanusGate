"""资产服务：CRUD + 连接测试。"""
import socket
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset


class AssetService:

    @staticmethod
    async def list_assets(db: AsyncSession, skip: int = 0, limit: int = 50) -> list[Asset]:
        result = await db.execute(
            select(Asset).offset(skip).limit(limit).order_by(Asset.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_asset(db: AsyncSession, asset_id: int) -> Asset | None:
        result = await db.execute(select(Asset).where(Asset.id == asset_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create_asset(db: AsyncSession, data: dict[str, Any]) -> Asset:
        asset = Asset(**data)
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def update_asset(
        db: AsyncSession, asset_id: int, data: dict[str, Any]
    ) -> Asset | None:
        result = await db.execute(select(Asset).where(Asset.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            return None
        for key, value in data.items():
            if hasattr(asset, key) and value is not None:
                setattr(asset, key, value)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def delete_asset(db: AsyncSession, asset_id: int) -> bool:
        result = await db.execute(select(Asset).where(Asset.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            return False
        await db.delete(asset)
        await db.commit()
        return True

    @staticmethod
    async def test_connection(
        address: str, port: int, timeout: float = 5.0
    ) -> dict[str, Any]:
        try:
            sock = socket.create_connection((address, port), timeout=timeout)
            sock.close()
            return {"reachable": True, "error": ""}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
