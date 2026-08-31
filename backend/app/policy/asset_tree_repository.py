"""#t64 资产树 / AssetPermission 的租户 scope 加载。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_tree import AssetPermissionModel, NodeModel
from app.tenancy.scope import ActorScope, scoped_select


class AssetTreeRepository:
    """按租户加载节点、授权与资产挂载。查询一律 scoped_select。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_nodes(self, actor_scope: ActorScope) -> list[NodeModel]:
        result = await self._session.execute(
            scoped_select(NodeModel, actor_scope).order_by(NodeModel.id.asc())
        )
        return list(result.scalars().all())

    async def list_permissions(self, actor_scope: ActorScope) -> list[AssetPermissionModel]:
        result = await self._session.execute(
            scoped_select(AssetPermissionModel, actor_scope).order_by(
                AssetPermissionModel.id.asc()
            )
        )
        return list(result.scalars().all())

    async def list_asset_node_ids(
        self, actor_scope: ActorScope
    ) -> dict[str, str | None]:
        """asset_id(str) → node_id（未分组为 None）。"""

        result = await self._session.execute(scoped_select(Asset, actor_scope))
        mapping: dict[str, str | None] = {}
        for asset in result.scalars().all():
            mapping[str(asset.id)] = asset.node_id or None
        return mapping
