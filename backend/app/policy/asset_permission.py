"""#t64 AssetPermission 判定与可见性查询核心（无旁路）。

父节点授权覆盖子孙资产；未分组资产只吃直接授权。过期视为不存在。
admin 不在本模块获得任何额外放行。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.models.asset_tree import (
    ASSET_RESOURCE,
    CONNECT_ACTION,
    NODE_RESOURCE,
    AssetPermissionModel,
    NodeModel,
)

CONNECT_ACTIONS = frozenset({CONNECT_ACTION, "session.connect", "asset.connect"})


def parse_ancestor_ids(node: NodeModel) -> list[str]:
    try:
        raw = json.loads(node.ancestor_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item]


def is_root(node: NodeModel) -> bool:
    return node.parent_id is None


def node_covers_asset_node(
    perm_node_id: str,
    asset_node_id: str | None,
    nodes_by_id: dict[str, NodeModel],
) -> bool:
    """节点 perm 是否覆盖挂在 asset_node_id 上的资产。"""

    if not asset_node_id:
        return False
    if perm_node_id == asset_node_id:
        return True
    node = nodes_by_id.get(asset_node_id)
    if node is None or is_root(node):
        return False
    return perm_node_id in parse_ancestor_ids(node)


def _selector_matches(configured: str, actual: str) -> bool:
    return configured == "" or configured == "*" or configured == actual


def _is_expired(permission: AssetPermissionModel, now: datetime) -> bool:
    if permission.expires_at is None:
        return False
    expires = permission.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    return expires <= current


def find_effective_connect_permission(
    *,
    subject_id: str,
    subject_group_ids: list[str] | tuple[str, ...] | None = None,
    tenant_id: str,
    asset_id: str,
    asset_node_id: str | None,
    account_id: str | None = "",
    protocol: str | None = "",
    permissions: list[AssetPermissionModel] | None = None,
    nodes_by_id: dict[str, NodeModel] | None = None,
    now: datetime | None = None,
) -> tuple[AssetPermissionModel | None, str]:
    """返回命中的 permission 与继承说明（``direct`` 或 ``node:<id>``）。

    ``account_id`` / ``protocol`` 为 ``None`` 时不按账号/协议收窄（使用面列表：
    任一有效 connect 即可见）。无命中返回 ``(None, "")``。根节点授权被忽略。
    """

    current = now or datetime.now(UTC)
    permissions = permissions or []
    nodes_by_id = nodes_by_id or {}
    for permission in permissions:
        if permission.tenant_id != tenant_id:
            continue
        subject_type = getattr(permission, "subject_type", "user") or "user"
        if subject_type == "user":
            subject_matches = permission.subject_id == subject_id
        elif subject_type == "user_group":
            subject_matches = permission.subject_id in (subject_group_ids or ())
        else:
            subject_matches = False
        if not subject_matches:
            continue
        if permission.action != CONNECT_ACTION:
            continue
        if _is_expired(permission, current):
            continue
        if account_id is not None and not _selector_matches(permission.account_id, account_id):
            continue
        if protocol is not None and not _selector_matches(permission.protocol, protocol):
            continue
        if permission.resource_type == ASSET_RESOURCE:
            if permission.resource_id == asset_id:
                return permission, "direct"
            continue
        if permission.resource_type != NODE_RESOURCE:
            continue
        node = nodes_by_id.get(permission.resource_id)
        if node is None or is_root(node):
            continue
        if node_covers_asset_node(permission.resource_id, asset_node_id, nodes_by_id):
            return permission, f"node:{permission.resource_id}"
    return None, ""


def connectable_asset_ids(
    *,
    subject_id: str,
    subject_group_ids: list[str] | tuple[str, ...] | None = None,
    tenant_id: str,
    assets: list[tuple[str, str | None]],
    permissions: list[AssetPermissionModel],
    nodes_by_id: dict[str, NodeModel],
    now: datetime | None = None,
) -> set[str]:
    """使用面可见资产：当前有效 connect。过期/未授权不当存在。"""

    visible: set[str] = set()
    for asset_id, node_id in assets:
        matched, _path = find_effective_connect_permission(
            subject_id=subject_id,
            subject_group_ids=subject_group_ids,
            tenant_id=tenant_id,
            asset_id=asset_id,
            asset_node_id=node_id,
            account_id=None,
            protocol=None,
            permissions=permissions,
            nodes_by_id=nodes_by_id,
            now=now,
        )
        if matched is not None:
            visible.add(asset_id)
    return visible


def request_account_protocol(request: Any) -> tuple[str, str]:
    ctx = getattr(request, "context", None)
    if not isinstance(ctx, dict):
        return "", ""
    return str(ctx.get("account_id") or ""), str(ctx.get("protocol") or "")
