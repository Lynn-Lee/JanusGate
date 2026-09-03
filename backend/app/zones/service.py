"""#t67 网域与网关服务：随机选取活跃网关、连通性探测。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.zone import ZoneGatewayModel, ZoneModel
from app.services.asset import AssetService
from app.tenancy.scope import ActorScope, scoped_select


def new_zone_id() -> str:
    """生成租户内唯一的网域 ID。"""

    return f"zone_{uuid4().hex[:12]}"


async def list_zones(db: AsyncSession, scope: ActorScope) -> list[ZoneModel]:
    """列出当前租户的全部网域。"""

    result = await db.execute(
        scoped_select(ZoneModel, scope).order_by(ZoneModel.name, ZoneModel.id)
    )
    return list(result.scalars().all())


async def get_zone(db: AsyncSession, scope: ActorScope, zone_id: str) -> ZoneModel | None:
    """按 ID 获取网域；跨租户返回 ``None``（调用方应映射为 404）。"""

    result = await db.execute(
        scoped_select(ZoneModel, scope).where(ZoneModel.id == zone_id)
    )
    return result.scalar_one_or_none()


async def create_zone(
    db: AsyncSession, scope: ActorScope, *, name: str, is_active: bool = True
) -> ZoneModel:
    """创建网域。"""

    zone = ZoneModel(
        id=new_zone_id(),
        tenant_id=scope.tenant_id,
        name=name.strip(),
        is_active=is_active,
    )
    db.add(zone)
    await db.commit()
    await db.refresh(zone)
    return zone


async def delete_zone(db: AsyncSession, scope: ActorScope, zone_id: str) -> bool:
    """删除网域及其网关关联；仍有关联资产时拒绝删除。"""

    zone = await get_zone(db, scope, zone_id)
    if zone is None:
        return False

    asset_count = await db.execute(
        scoped_select(Asset, scope).where(Asset.zone_id == zone_id)
    )
    if asset_count.scalars().first() is not None:
        raise ValueError("ZONE_HAS_ASSETS")

    gateways = await db.execute(
        scoped_select(ZoneGatewayModel, scope).where(ZoneGatewayModel.zone_id == zone_id)
    )
    for gateway in gateways.scalars().all():
        await db.delete(gateway)
    await db.delete(zone)
    await db.commit()
    return True


async def list_zone_gateways(
    db: AsyncSession, scope: ActorScope, zone_id: str
) -> list[ZoneGatewayModel]:
    """列出网域下的全部网关关联。"""

    result = await db.execute(
        scoped_select(ZoneGatewayModel, scope)
        .where(ZoneGatewayModel.zone_id == zone_id)
        .order_by(ZoneGatewayModel.id)
    )
    return list(result.scalars().all())


async def add_zone_gateway(
    db: AsyncSession,
    scope: ActorScope,
    *,
    zone_id: str,
    gateway_asset_id: int,
    gateway_account_id: int | None = None,
) -> ZoneGatewayModel:
    """把已有资产登记为网域网关。"""

    zone = await get_zone(db, scope, zone_id)
    if zone is None:
        raise LookupError("ZONE_NOT_FOUND")

    asset = await db.execute(
        scoped_select(Asset, scope).where(Asset.id == gateway_asset_id)
    )
    gateway_asset = asset.scalar_one_or_none()
    if gateway_asset is None or not gateway_asset.is_active:
        raise LookupError("GATEWAY_ASSET_NOT_FOUND")

    if gateway_account_id is not None:
        account = await db.execute(
            scoped_select(Account, scope)
            .where(Account.id == gateway_account_id)
            .where(Account.asset_id == gateway_asset_id)
        )
        if account.scalar_one_or_none() is None:
            raise LookupError("GATEWAY_ACCOUNT_NOT_FOUND")

    existing = await db.execute(
        scoped_select(ZoneGatewayModel, scope)
        .where(ZoneGatewayModel.zone_id == zone_id)
        .where(ZoneGatewayModel.gateway_asset_id == gateway_asset_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError("GATEWAY_ALREADY_REGISTERED")

    row = ZoneGatewayModel(
        tenant_id=scope.tenant_id,
        zone_id=zone_id,
        gateway_asset_id=gateway_asset_id,
        gateway_account_id=gateway_account_id,
        is_active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def remove_zone_gateway(
    db: AsyncSession, scope: ActorScope, *, zone_id: str, gateway_asset_id: int
) -> bool:
    """从网域移除网关关联。"""

    result = await db.execute(
        scoped_select(ZoneGatewayModel, scope)
        .where(ZoneGatewayModel.zone_id == zone_id)
        .where(ZoneGatewayModel.gateway_asset_id == gateway_asset_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def probe_gateway(
    db: AsyncSession, scope: ActorScope, *, zone_id: str, gateway_asset_id: int
) -> ZoneGatewayModel:
    """对指定网关做 TCP 连通性探测并更新探测状态。"""

    result = await db.execute(
        scoped_select(ZoneGatewayModel, scope)
        .where(ZoneGatewayModel.zone_id == zone_id)
        .where(ZoneGatewayModel.gateway_asset_id == gateway_asset_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise LookupError("ZONE_GATEWAY_NOT_FOUND")

    asset = await db.execute(
        scoped_select(Asset, scope).where(Asset.id == gateway_asset_id)
    )
    gateway_asset = asset.scalar_one_or_none()
    if gateway_asset is None:
        raise LookupError("GATEWAY_ASSET_NOT_FOUND")

    probe = await AssetService.probe_registered_host(
        gateway_asset.address, gateway_asset.port
    )
    row.last_probe_at = datetime.now(UTC)
    if probe.get("reachable"):
        row.probe_status = "reachable"
        row.probe_error = ""
    else:
        row.probe_status = "unreachable"
        row.probe_error = str(probe.get("error") or "probe failed")
    await db.commit()
    await db.refresh(row)
    return row


async def pick_random_active_gateway(
    db: AsyncSession, scope: ActorScope, zone_id: str
) -> tuple[ZoneGatewayModel, Asset] | None:
    """从网域中随机选取一条活跃且资产仍有效的网关关联。

    仅返回 ``ZoneGatewayModel.is_active``、关联网关资产 ``is_active`` 均为真的条目；
    若配置了 ``gateway_account_id`` 则一并校验账号仍处于 active。
    """

    zone = await get_zone(db, scope, zone_id)
    if zone is None or not zone.is_active:
        return None

    result = await db.execute(
        scoped_select(ZoneGatewayModel, scope)
        .where(ZoneGatewayModel.zone_id == zone_id)
        .where(ZoneGatewayModel.is_active.is_(True))
    )
    candidates: list[tuple[ZoneGatewayModel, Asset]] = []
    for row in result.scalars().all():
        asset_result = await db.execute(
            scoped_select(Asset, scope).where(Asset.id == row.gateway_asset_id)
        )
        gateway_asset = asset_result.scalar_one_or_none()
        if gateway_asset is None or not gateway_asset.is_active:
            continue
        if row.gateway_account_id is not None:
            account_result = await db.execute(
                scoped_select(Account, scope).where(Account.id == row.gateway_account_id)
            )
            account = account_result.scalar_one_or_none()
            if account is None or account.status != "active":
                continue
        candidates.append((row, gateway_asset))

    if not candidates:
        return None
    return secrets.choice(candidates)


async def resolve_gateway_account(
    db: AsyncSession,
    scope: ActorScope,
    *,
    gateway_row: ZoneGatewayModel,
    gateway_asset: Asset,
    protocol: str,
) -> Account | None:
    """解析网关登录账号：优先显式 ``gateway_account_id``，否则取首个 active SSH 账号。"""

    if gateway_row.gateway_account_id is not None:
        result = await db.execute(
            scoped_select(Account, scope).where(Account.id == gateway_row.gateway_account_id)
        )
        account = result.scalar_one_or_none()
        if account is not None and account.status == "active":
            return account

    stmt = (
        scoped_select(Account, scope)
        .where(Account.asset_id == gateway_asset.id)
        .where(Account.status == "active")
        .order_by(Account.id)
    )
    if protocol:
        typed = await db.execute(stmt.where(Account.protocol == protocol))
        account = typed.scalars().first()
        if account is not None:
            return account
    result = await db.execute(stmt)
    return result.scalars().first()
