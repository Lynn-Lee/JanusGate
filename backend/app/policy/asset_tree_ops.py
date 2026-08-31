"""#t64 树操作：根容器、祖先链、移动/移出后谁会失去 connect。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_tree import AssetPermissionModel, NodeModel
from app.policy.asset_permission import (
    find_effective_connect_permission,
    is_root,
    parse_ancestor_ids,
)
from app.tenancy.scope import ActorScope, scoped_select

ROOT_NAME = "根"


def dump_ancestor_ids(ids: list[str]) -> str:
    return json.dumps(ids)


def new_node_id() -> str:
    return f"node_{uuid4().hex}"


def new_permission_id() -> str:
    return f"ap_{uuid4().hex}"


async def list_nodes(db: AsyncSession, actor_scope: ActorScope) -> list[NodeModel]:
    result = await db.execute(scoped_select(NodeModel, actor_scope).order_by(NodeModel.id.asc()))
    return list(result.scalars().all())


async def list_assets(db: AsyncSession, actor_scope: ActorScope) -> list[Asset]:
    result = await db.execute(scoped_select(Asset, actor_scope).order_by(Asset.id.asc()))
    return list(result.scalars().all())


async def list_permissions(
    db: AsyncSession, actor_scope: ActorScope
) -> list[AssetPermissionModel]:
    result = await db.execute(
        scoped_select(AssetPermissionModel, actor_scope).order_by(AssetPermissionModel.id.asc())
    )
    return list(result.scalars().all())


async def ensure_tenant_root(db: AsyncSession, actor_scope: ActorScope) -> NodeModel:
    nodes = await list_nodes(db, actor_scope)
    roots = [node for node in nodes if is_root(node)]
    if roots:
        return roots[0]
    root = NodeModel(
        id=new_node_id(),
        tenant_id=actor_scope.tenant_id,
        parent_id=None,
        name=ROOT_NAME,
        ancestor_ids_json="[]",
    )
    db.add(root)
    await db.flush()
    return root


def nodes_by_id(nodes: list[NodeModel]) -> dict[str, NodeModel]:
    return {node.id: node for node in nodes}


def descendant_ids(node_id: str, nodes: list[NodeModel]) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if node_id in parse_ancestor_ids(node):
            found.add(node.id)
    return found


def subtree_ids(node_id: str, nodes: list[NodeModel]) -> set[str]:
    return {node_id} | descendant_ids(node_id, nodes)


def apply_move_in_memory(node: NodeModel, new_parent: NodeModel, nodes: list[NodeModel]) -> None:
    """就地更新 node 及其子孙的 parent/ancestors（调用方已校验）。"""

    new_prefix = [*parse_ancestor_ids(new_parent), new_parent.id]
    node.parent_id = new_parent.id
    node.ancestor_ids_json = dump_ancestor_ids(new_prefix)
    for other in nodes:
        if other.id == node.id:
            continue
        ancestors = parse_ancestor_ids(other)
        if node.id not in ancestors:
            continue
        idx = ancestors.index(node.id)
        other.ancestor_ids_json = dump_ancestor_ids(new_prefix + ancestors[idx:])


def connect_subject_ids(
    *,
    asset_id: str,
    asset_node_id: str | None,
    permissions: list[AssetPermissionModel],
    nodes: dict[str, NodeModel],
    tenant_id: str,
    now: datetime | None = None,
) -> set[str]:
    subjects = {
        (getattr(perm, "subject_type", "user") or "user", perm.subject_id)
        for perm in permissions
        if perm.tenant_id == tenant_id
    }
    visible: set[str] = set()
    current = now or datetime.now(UTC)
    for subject_type, subject_id in subjects:
        matched, _path = find_effective_connect_permission(
            subject_id=subject_id,
            subject_group_ids=[subject_id] if subject_type == "user_group" else None,
            tenant_id=tenant_id,
            asset_id=asset_id,
            asset_node_id=asset_node_id,
            account_id=None,
            protocol=None,
            permissions=permissions,
            nodes_by_id=nodes,
            now=current,
        )
        if matched is not None:
            visible.add(subject_id)
    return visible


def lost_connect(
    *,
    assets: list[Asset],
    before_node_ids: dict[str, str | None],
    after_node_ids: dict[str, str | None],
    before_nodes: dict[str, NodeModel],
    after_nodes: dict[str, NodeModel],
    permissions: list[AssetPermissionModel],
    tenant_id: str,
) -> list[dict[str, str]]:
    lost: list[dict[str, str]] = []
    for asset in assets:
        asset_id = str(asset.id)
        before = connect_subject_ids(
            asset_id=asset_id,
            asset_node_id=before_node_ids.get(asset_id),
            permissions=permissions,
            nodes=before_nodes,
            tenant_id=tenant_id,
        )
        after = connect_subject_ids(
            asset_id=asset_id,
            asset_node_id=after_node_ids.get(asset_id),
            permissions=permissions,
            nodes=after_nodes,
            tenant_id=tenant_id,
        )
        for subject_id in sorted(before - after):
            lost.append(
                {
                    "subject_id": subject_id,
                    "asset_id": asset_id,
                    "asset_name": asset.name,
                }
            )
    return lost
