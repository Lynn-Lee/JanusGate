"""#t64 资产树与 AssetPermission 管理 API。

管理面用：树 CRUD、直属资产挂/移出、谁能连。不新开导航。跨租户 404，
文案不用「没有权限」。租户根只当容器。判定仍只走 PolicyDecisionService。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.asset_tree_schemas import (
    ConnectImpactResponse,
    HangAsset,
    NodeCreate,
    NodeListResponse,
    NodeMove,
    NodeRename,
    NodeResponse,
    PermissionCreate,
    PermissionListResponse,
    PermissionResponse,
    TreeAssetListResponse,
    TreeAssetResponse,
    UngroupAsset,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.asset import Asset
from app.models.asset_tree import (
    ASSET_RESOURCE,
    NODE_RESOURCE,
    AssetPermissionModel,
    NodeModel,
)
from app.policy.asset_permission import is_root, parse_ancestor_ids
from app.policy.asset_tree_ops import (
    apply_move_in_memory,
    descendant_ids,
    dump_ancestor_ids,
    ensure_tenant_root,
    list_assets,
    list_nodes,
    list_permissions,
    lost_connect,
    new_node_id,
    new_permission_id,
    nodes_by_id,
)
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(tags=["资产树"])

ROOT_COPY = "根节点只用于组织树，不能挂资产或授权。"
DELETE_HAS_CHILDREN = "先移走或删除子节点。"
DELETE_HAS_ASSETS = "先把资产移出或移到其他节点。"


def _require_assets_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _not_found(code: str) -> NoReturn:
    raise HTTPException(status_code=404, detail=code)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _node_response(node: NodeModel) -> NodeResponse:
    return NodeResponse(
        id=node.id,
        tenant_id=node.tenant_id,
        parent_id=node.parent_id,
        name=node.name,
        is_root=is_root(node),
        ancestor_ids=parse_ancestor_ids(node),
    )


def _expired(permission: AssetPermissionModel, now: datetime | None = None) -> bool:
    if permission.expires_at is None:
        return False
    current = now or datetime.now(UTC)
    expires = _as_utc(permission.expires_at)
    if expires is None:
        return False
    return expires <= (current if current.tzinfo else current.replace(tzinfo=UTC))


def _location_label(asset: Asset, nodes: dict[str, NodeModel]) -> str:
    if not asset.node_id:
        return "未分组"
    node = nodes.get(asset.node_id)
    if node is None:
        return "未分组"
    return f"现位于节点 {node.name}"


async def _get_node(
    db: AsyncSession, actor_scope: ActorScope, node_id: str
) -> NodeModel:
    result = await db.execute(
        scoped_select(NodeModel, actor_scope).where(NodeModel.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        _not_found("NODE_NOT_FOUND")
    return node


async def _get_asset(
    db: AsyncSession, actor_scope: ActorScope, asset_id: int
) -> Asset:
    result = await db.execute(
        scoped_select(Asset, actor_scope).where(Asset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        _not_found("ASSET_NOT_FOUND")
    return asset


async def _get_permission(
    db: AsyncSession, actor_scope: ActorScope, permission_id: str
) -> AssetPermissionModel:
    result = await db.execute(
        scoped_select(AssetPermissionModel, actor_scope).where(
            AssetPermissionModel.id == permission_id
        )
    )
    permission = result.scalar_one_or_none()
    if permission is None:
        _not_found("ASSET_PERMISSION_NOT_FOUND")
    return permission


def _clone_nodes(nodes: list[NodeModel]) -> list[NodeModel]:
    clones: list[NodeModel] = []
    for node in nodes:
        clones.append(
            NodeModel(
                id=node.id,
                tenant_id=node.tenant_id,
                parent_id=node.parent_id,
                name=node.name,
                ancestor_ids_json=node.ancestor_ids_json,
            )
        )
    return clones


@router.get("/asset-nodes/", response_model=NodeListResponse)
async def list_asset_nodes(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NodeListResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    await ensure_tenant_root(db, actor_scope)
    await db.commit()
    nodes = await list_nodes(db, actor_scope)
    return NodeListResponse(items=[_node_response(node) for node in nodes])


@router.post(
    "/asset-nodes/",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset_node(
    data: NodeCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NodeResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    root = await ensure_tenant_root(db, actor_scope)
    parent_id = data.parent_id or root.id
    parent = await _get_node(db, actor_scope, parent_id)
    node = NodeModel(
        id=new_node_id(),
        tenant_id=actor_scope.tenant_id,
        parent_id=parent.id,
        name=data.name,
        ancestor_ids_json=dump_ancestor_ids([*parse_ancestor_ids(parent), parent.id]),
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return _node_response(node)


@router.patch("/asset-nodes/{node_id}", response_model=NodeResponse)
async def rename_asset_node(
    node_id: str,
    data: NodeRename,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NodeResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    node = await _get_node(db, actor_scope, node_id)
    node.name = data.name
    await db.commit()
    await db.refresh(node)
    return _node_response(node)


@router.get("/asset-nodes/{node_id}/move-impact", response_model=ConnectImpactResponse)
async def node_move_impact(
    node_id: str,
    parent_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectImpactResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    return ConnectImpactResponse(
        lost=await _node_move_lost(db, actor_scope, node_id, parent_id)
    )


@router.post("/asset-nodes/{node_id}/move", response_model=NodeResponse)
async def move_asset_node(
    node_id: str,
    data: NodeMove,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> NodeResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    node, new_parent, nodes = await _validate_move(db, actor_scope, node_id, data.parent_id)
    apply_move_in_memory(node, new_parent, nodes)
    await db.commit()
    await db.refresh(node)
    return _node_response(node)


@router.delete("/asset-nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    nodes = await list_nodes(db, actor_scope)
    if descendant_ids(node.id, nodes):
        raise HTTPException(status_code=400, detail=DELETE_HAS_CHILDREN)
    assets = await list_assets(db, actor_scope)
    if any(asset.node_id == node.id for asset in assets):
        raise HTTPException(status_code=400, detail=DELETE_HAS_ASSETS)
    permissions = await list_permissions(db, actor_scope)
    for permission in permissions:
        if permission.resource_type == NODE_RESOURCE and permission.resource_id == node.id:
            await db.delete(permission)
    await db.delete(node)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/asset-nodes/{node_id}/assets", response_model=TreeAssetListResponse)
async def list_node_assets(
    node_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> TreeAssetListResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    assets = [asset for asset in await list_assets(db, actor_scope) if asset.node_id == node.id]
    return TreeAssetListResponse(
        items=[
            TreeAssetResponse(
                id=asset.id,
                name=asset.name,
                address=asset.address,
                node_id=asset.node_id,
                location_label=_location_label(asset, nodes),
            )
            for asset in assets
        ]
    )


@router.get("/asset-nodes/ungrouped-assets", response_model=TreeAssetListResponse)
async def list_ungrouped_assets(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> TreeAssetListResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    assets = await list_assets(db, actor_scope)
    return TreeAssetListResponse(
        items=[
            TreeAssetResponse(
                id=asset.id,
                name=asset.name,
                address=asset.address,
                node_id=asset.node_id,
                location_label=_location_label(asset, nodes),
            )
            for asset in assets
        ]
    )


@router.get("/asset-nodes/{node_id}/hang-impact", response_model=ConnectImpactResponse)
async def hang_asset_impact(
    node_id: str,
    asset_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectImpactResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    return ConnectImpactResponse(
        lost=await _hang_lost(db, actor_scope, node_id, asset_id)
    )


@router.post("/asset-nodes/{node_id}/assets", response_model=TreeAssetResponse)
async def hang_asset(
    node_id: str,
    data: HangAsset,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> TreeAssetResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    asset = await _get_asset(db, actor_scope, data.asset_id)
    asset.node_id = node.id
    await db.commit()
    await db.refresh(asset)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    return TreeAssetResponse(
        id=asset.id,
        name=asset.name,
        address=asset.address,
        node_id=asset.node_id,
        location_label=_location_label(asset, nodes),
    )


@router.get("/asset-nodes/ungroup-impact", response_model=ConnectImpactResponse)
async def ungroup_impact(
    asset_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectImpactResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    return ConnectImpactResponse(lost=await _ungroup_lost(db, actor_scope, asset_id))


@router.post("/asset-nodes/ungroup", response_model=TreeAssetResponse)
async def ungroup_asset(
    data: UngroupAsset,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> TreeAssetResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    asset = await _get_asset(db, actor_scope, data.asset_id)
    asset.node_id = None
    await db.commit()
    await db.refresh(asset)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    return TreeAssetResponse(
        id=asset.id,
        name=asset.name,
        address=asset.address,
        node_id=asset.node_id,
        location_label=_location_label(asset, nodes),
    )


@router.get("/asset-nodes/{node_id}/permissions", response_model=PermissionListResponse)
async def list_node_permissions(
    node_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> PermissionListResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    return PermissionListResponse(
        items=await _permissions_for_resource(db, actor_scope, NODE_RESOURCE, node.id, node)
    )


@router.post(
    "/asset-nodes/{node_id}/permissions",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_node_permission(
    node_id: str,
    data: PermissionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> PermissionResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    return await _create_permission(
        db, actor_scope, NODE_RESOURCE, node.id, data, inherited=False, node=node
    )


@router.get(
    "/asset-permissions/by-asset/{asset_id}",
    response_model=PermissionListResponse,
)
async def list_asset_permissions(
    asset_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> PermissionListResponse:
    _require_assets_permission(user, "assets:read")
    actor_scope = actor_scope_from_user(user)
    asset = await _get_asset(db, actor_scope, asset_id)
    items = await _permissions_for_resource(
        db, actor_scope, ASSET_RESOURCE, str(asset.id), None
    )
    if asset.node_id:
        node = await _get_node(db, actor_scope, asset.node_id)
        items.extend(
            await _permissions_for_resource(
                db, actor_scope, NODE_RESOURCE, node.id, node, inherited_view=True
            )
        )
        for ancestor_id in parse_ancestor_ids(node):
            ancestor = await _get_node(db, actor_scope, ancestor_id)
            if is_root(ancestor):
                continue
            items.extend(
                await _permissions_for_resource(
                    db, actor_scope, NODE_RESOURCE, ancestor.id, ancestor, inherited_view=True
                )
            )
    return PermissionListResponse(items=items)


@router.post(
    "/asset-permissions/by-asset/{asset_id}",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset_permission(
    asset_id: int,
    data: PermissionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> PermissionResponse:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    asset = await _get_asset(db, actor_scope, asset_id)
    return await _create_permission(
        db, actor_scope, ASSET_RESOURCE, str(asset.id), data, inherited=False, node=None
    )


@router.delete(
    "/asset-permissions/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_asset_permission(
    permission_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_assets_permission(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    permission = await _get_permission(db, actor_scope, permission_id)
    await db.delete(permission)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _create_permission(
    db: AsyncSession,
    actor_scope: ActorScope,
    resource_type: str,
    resource_id: str,
    data: PermissionCreate,
    *,
    inherited: bool,
    node: NodeModel | None,
) -> PermissionResponse:
    permission = AssetPermissionModel(
        id=new_permission_id(),
        tenant_id=actor_scope.tenant_id,
        subject_id=data.subject_id,
        subject_type=data.subject_type,
        resource_type=resource_type,
        resource_id=resource_id,
        account_id=data.account_id or "",
        protocol=data.protocol or "",
        action=data.action,
        expires_at=data.expires_at,
        from_ticket=data.from_ticket,
    )
    db.add(permission)
    await db.commit()
    await db.refresh(permission)
    return _permission_response(permission, inherited=inherited, node=node)


def _permission_response(
    permission: AssetPermissionModel,
    *,
    inherited: bool,
    node: NodeModel | None,
) -> PermissionResponse:
    return PermissionResponse(
        id=permission.id,
        tenant_id=permission.tenant_id,
        subject_id=permission.subject_id,
        subject_type=permission.subject_type,
        resource_type=permission.resource_type,
        resource_id=permission.resource_id,
        account_id=permission.account_id,
        protocol=permission.protocol,
        action=permission.action,
        expires_at=_as_utc(permission.expires_at),
        from_ticket=permission.from_ticket,
        expired=_expired(permission),
        inherited=inherited,
        inherited_from_node_id=node.id if inherited and node is not None else None,
        inherited_from_node_name=node.name if inherited and node is not None else None,
    )


async def _permissions_for_resource(
    db: AsyncSession,
    actor_scope: ActorScope,
    resource_type: str,
    resource_id: str,
    node: NodeModel | None,
    *,
    inherited_view: bool = False,
) -> list[PermissionResponse]:
    permissions = await list_permissions(db, actor_scope)
    items: list[PermissionResponse] = []
    for permission in permissions:
        if permission.resource_type != resource_type or permission.resource_id != resource_id:
            continue
        items.append(
            _permission_response(
                permission,
                inherited=inherited_view,
                node=node if inherited_view else None,
            )
        )
    return items


async def _validate_move(
    db: AsyncSession, actor_scope: ActorScope, node_id: str, parent_id: str
) -> tuple[NodeModel, NodeModel, list[NodeModel]]:
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    new_parent = await _get_node(db, actor_scope, parent_id)
    if new_parent.id == node.id:
        raise HTTPException(status_code=400, detail="不能移动到自身。")
    nodes = await list_nodes(db, actor_scope)
    if new_parent.id in descendant_ids(node.id, nodes):
        raise HTTPException(status_code=400, detail="不能移动到自己的子孙节点。")
    return node, new_parent, nodes


async def _node_move_lost(
    db: AsyncSession, actor_scope: ActorScope, node_id: str, parent_id: str
) -> list[dict[str, str]]:
    node, new_parent, nodes = await _validate_move(db, actor_scope, node_id, parent_id)
    assets = await list_assets(db, actor_scope)
    permissions = await list_permissions(db, actor_scope)
    before_ids = {str(asset.id): asset.node_id for asset in assets}
    before_map = nodes_by_id(nodes)
    clones = _clone_nodes(nodes)
    clone_node = next(item for item in clones if item.id == node.id)
    clone_parent = next(item for item in clones if item.id == new_parent.id)
    apply_move_in_memory(clone_node, clone_parent, clones)
    return lost_connect(
        assets=assets,
        before_node_ids=before_ids,
        after_node_ids=before_ids,
        before_nodes=before_map,
        after_nodes=nodes_by_id(clones),
        permissions=permissions,
        tenant_id=actor_scope.tenant_id,
    )


async def _hang_lost(
    db: AsyncSession, actor_scope: ActorScope, node_id: str, asset_id: int
) -> list[dict[str, str]]:
    node = await _get_node(db, actor_scope, node_id)
    if is_root(node):
        raise HTTPException(status_code=400, detail=ROOT_COPY)
    asset = await _get_asset(db, actor_scope, asset_id)
    assets = await list_assets(db, actor_scope)
    permissions = await list_permissions(db, actor_scope)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    before_ids = {str(item.id): item.node_id for item in assets}
    after_ids = dict(before_ids)
    after_ids[str(asset.id)] = node.id
    return lost_connect(
        assets=assets,
        before_node_ids=before_ids,
        after_node_ids=after_ids,
        before_nodes=nodes,
        after_nodes=nodes,
        permissions=permissions,
        tenant_id=actor_scope.tenant_id,
    )


async def _ungroup_lost(
    db: AsyncSession, actor_scope: ActorScope, asset_id: int
) -> list[dict[str, str]]:
    asset = await _get_asset(db, actor_scope, asset_id)
    assets = await list_assets(db, actor_scope)
    permissions = await list_permissions(db, actor_scope)
    nodes = nodes_by_id(await list_nodes(db, actor_scope))
    before_ids = {str(item.id): item.node_id for item in assets}
    after_ids = dict(before_ids)
    after_ids[str(asset.id)] = None
    return lost_connect(
        assets=assets,
        before_node_ids=before_ids,
        after_node_ids=after_ids,
        before_nodes=nodes,
        after_nodes=nodes,
        permissions=permissions,
        tenant_id=actor_scope.tenant_id,
    )
