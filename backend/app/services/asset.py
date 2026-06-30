"""资产服务：CRUD + 连接测试。"""
import ipaddress
import socket

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
]


def _is_private_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        return any(ip in net for net in _PRIVATE_RANGES)
    except ValueError:
        return False


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
    async def create_asset(db: AsyncSession, data: dict) -> Asset:
        asset = Asset(**data)
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def update_asset(db: AsyncSession, asset_id: int, data: dict) -> Asset | None:
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
    async def test_connection(address: str, port: int, timeout: float = 5.0) -> dict:
        if _is_private_ip(address):
            return {"reachable": False, "error": "SSRF protection: private/internal IP blocked"}
        try:
            sock = socket.create_connection((address, port), timeout=timeout)
            sock.close()
            return {"reachable": True, "error": ""}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
