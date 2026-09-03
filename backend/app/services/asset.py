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


def _resolve_public_targets(address: str, port: int) -> list[tuple[str, int]]:
    infos = socket.getaddrinfo(address, port, type=socket.SOCK_STREAM)
    targets: list[tuple[str, int]] = []
    for info in infos:
        resolved_ip = str(info[4][0])
        if _is_private_ip(resolved_ip):
            raise ValueError("SSRF protection: private/internal IP blocked")
        targets.append((resolved_ip, int(info[4][1])))
    return targets


def _connect_to_any_target(
    targets: list[tuple[str, int]], timeout: float
) -> socket.socket:
    last_error: Exception | None = None
    for target in targets:
        try:
            return socket.create_connection(target, timeout=timeout)
        except Exception as exc:  # pragma: no cover - exercised through final error path
            last_error = exc
    if last_error:
        raise last_error
    raise socket.gaierror("no address resolved")


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
            if not hasattr(asset, key):
                continue
            # ``zone_id=None`` 表示解绑网域，必须写入；其它字段仍忽略 None。
            if value is None and key != "zone_id":
                continue
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
        """Arbitrary-target connectivity check with SSRF private-IP blocking.

        Use only for untrusted / allowlisted addresses. For inventory-registered
        jump hosts (incl. RFC1918), call :meth:`probe_registered_host` instead.
        """

        if _is_private_ip(address):
            return {"reachable": False, "error": "SSRF protection: private/internal IP blocked"}
        try:
            targets = await asyncio.to_thread(_resolve_public_targets, address, port)
            sock = await asyncio.to_thread(_connect_to_any_target, targets, timeout)
            sock.close()
            return {"reachable": True, "error": ""}
        except ValueError as e:
            return {"reachable": False, "error": str(e)}
        except Exception as e:
            return {"reachable": False, "error": str(e)}

    @staticmethod
    async def probe_registered_host(
        address: str, port: int, timeout: float = 5.0
    ) -> dict[str, Any]:
        """TCP probe for inventory-registered hosts, including private jump hosts.

        Unlike ``test_connection``, this does **not** apply SSRF private-IP
        blocking: the address must already be an approved inventory asset (e.g.
        a zone gateway), not an arbitrary caller-supplied URL.
        """

        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo, address, port, type=socket.SOCK_STREAM
            )
            targets = [(str(info[4][0]), int(info[4][1])) for info in infos]
            if not targets:
                return {"reachable": False, "error": "no address resolved"}
            sock = await asyncio.to_thread(_connect_to_any_target, targets, timeout)
            sock.close()
            return {"reachable": True, "error": ""}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
