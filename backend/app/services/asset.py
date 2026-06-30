"""资产服务：CRUD + 连接测试。"""
import asyncio
import ipaddress
import socket
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset


def _is_private_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


def _resolves_to_private_ip(address: str, port: int) -> bool:
    try:
        infos = socket.getaddrinfo(address, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    return any(_is_private_ip(str(info[4][0])) for info in infos)


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
        if _is_private_ip(address) or _resolves_to_private_ip(address, port):
            return {"reachable": False, "error": "SSRF protection: private/internal IP blocked"}
        try:
            sock = await asyncio.to_thread(socket.create_connection, (address, port), timeout=timeout)
            sock.close()
            return {"reachable": True, "error": ""}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
