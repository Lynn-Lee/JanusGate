"""#t64：AssetPermission 判定与可见性（内存模型，不落库）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.models.asset_tree import (
    ASSET_RESOURCE,
    CONNECT_ACTION,
    NODE_RESOURCE,
    AssetPermissionModel,
    NodeModel,
)
from app.policy.asset_permission import (
    connectable_asset_ids,
    find_effective_connect_permission,
)
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import (
    PolicyDecision,
    PolicyDecisionRequest,
    ResourceRef,
    SubjectRef,
)


def _node(
    node_id: str,
    *,
    parent_id: str | None,
    ancestor_ids: list[str],
    tenant_id: str = "tenant-a",
    name: str | None = None,
) -> NodeModel:
    return NodeModel(
        id=node_id,
        tenant_id=tenant_id,
        parent_id=parent_id,
        name=name or node_id,
        ancestor_ids_json=json.dumps(ancestor_ids),
    )


def _perm(
    perm_id: str,
    *,
    subject_id: str = "user-1",
    subject_type: str = "user",
    resource_type: str = NODE_RESOURCE,
    resource_id: str,
    tenant_id: str = "tenant-a",
    account_id: str = "",
    protocol: str = "",
    action: str = CONNECT_ACTION,
    from_ticket: str | None = None,
    expires_at: datetime | None = None,
) -> AssetPermissionModel:
    return AssetPermissionModel(
        id=perm_id,
        tenant_id=tenant_id,
        subject_id=subject_id,
        subject_type=subject_type,
        resource_type=resource_type,
        resource_id=resource_id,
        account_id=account_id,
        protocol=protocol,
        action=action,
        from_ticket=from_ticket,
        expires_at=expires_at,
    )


def _tree() -> dict[str, NodeModel]:
    root = _node("root", parent_id=None, ancestor_ids=[])
    folder = _node("folder", parent_id="root", ancestor_ids=["root"], name="folder")
    leaf = _node("leaf", parent_id="folder", ancestor_ids=["root", "folder"], name="leaf")
    other = _node("other", parent_id="root", ancestor_ids=["root"], name="other")
    return {n.id: n for n in (root, folder, leaf, other)}


def test_unauthorized_has_no_effective_permission() -> None:
    nodes = _tree()
    matched, path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="",
        protocol="",
        permissions=[],
        nodes_by_id=nodes,
    )
    assert matched is None
    assert path == ""


def test_direct_asset_permission_allows() -> None:
    nodes = _tree()
    perm = _perm("ap-direct", resource_type=ASSET_RESOURCE, resource_id="10")
    matched, path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="",
        protocol="",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is perm
    assert path == "direct"


def test_parent_node_permission_covers_descendant_asset() -> None:
    nodes = _tree()
    perm = _perm("ap-folder", resource_id="folder")
    matched, path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="",
        protocol="",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is perm
    assert path == "node:folder"


def test_node_permission_does_not_cover_sibling_branch() -> None:
    nodes = _tree()
    perm = _perm("ap-folder", resource_id="folder")
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="11",
        asset_node_id="other",
        account_id="",
        protocol="",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is None


def test_ungrouped_asset_only_gets_direct_permission() -> None:
    nodes = _tree()
    node_perm = _perm("ap-folder", resource_id="folder")
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="12",
        asset_node_id=None,
        account_id="",
        protocol="",
        permissions=[node_perm],
        nodes_by_id=nodes,
    )
    assert matched is None
    direct = _perm("ap-12", resource_type=ASSET_RESOURCE, resource_id="12")
    matched, path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="12",
        asset_node_id=None,
        account_id="",
        protocol="",
        permissions=[node_perm, direct],
        nodes_by_id=nodes,
    )
    assert matched is direct
    assert path == "direct"


def test_moving_off_tree_drops_node_inheritance_keeps_direct() -> None:
    nodes = _tree()
    node_perm = _perm("ap-folder", resource_id="folder")
    direct = _perm("ap-10", resource_type=ASSET_RESOURCE, resource_id="10")
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id=None,
        account_id="",
        protocol="",
        permissions=[node_perm, direct],
        nodes_by_id=nodes,
    )
    assert matched is direct


def test_expired_permission_does_not_match() -> None:
    nodes = _tree()
    perm = _perm(
        "ap-old",
        resource_type=ASSET_RESOURCE,
        resource_id="10",
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="",
        protocol="",
        permissions=[perm],
        nodes_by_id=nodes,
        now=datetime(2026, 8, 31, tzinfo=UTC),
    )
    assert matched is None


def test_root_node_permission_is_ignored() -> None:
    nodes = _tree()
    perm = _perm("ap-root", resource_id="root")
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="",
        protocol="",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is None


def test_group_permission_matches_subject_group_and_preserves_ticket_and_action() -> None:
    nodes = _tree()
    perm = _perm(
        "ap-group",
        subject_id="ops",
        subject_type="user_group",
        resource_type=ASSET_RESOURCE,
        resource_id="10",
        action="connect",
        from_ticket="ticket-64",
    )
    matched, path = find_effective_connect_permission(
        subject_id="user-1",
        subject_group_ids=["ops"],
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id=None,
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is perm
    assert path == "direct"
    assert matched.subject_type == "user_group"
    assert matched.from_ticket == "ticket-64"


def test_policy_decision_uses_group_ids_from_request_context() -> None:
    nodes = _tree()
    service = PolicyDecisionService(
        asset_permissions=[
            _perm(
                "ap-group",
                subject_id="ops",
                subject_type="user_group",
                resource_type=ASSET_RESOURCE,
                resource_id="10",
            )
        ],
        nodes=list(nodes.values()),
        asset_node_ids={"10": None},
    )
    result = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="10", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            context={"group_ids": ["ops"]},
            connector_trusted=True,
        )
    )
    assert result.decision is PolicyDecision.ALLOW
    assert any("permission:ap-group" in line for line in result.explain_trace)


def test_connectable_set_hides_unauthorized_and_expired() -> None:
    nodes = _tree()
    permissions = [
        _perm("ap-folder", resource_id="folder"),
        _perm(
            "ap-expired",
            resource_type=ASSET_RESOURCE,
            resource_id="99",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
    ]
    visible = connectable_asset_ids(
        subject_id="user-1",
        tenant_id="tenant-a",
        assets=[("10", "leaf"), ("11", "other"), ("99", None)],
        permissions=permissions,
        nodes_by_id=nodes,
    )
    assert visible == {"10"}


def test_empty_account_and_protocol_match_all() -> None:
    nodes = _tree()
    perm = _perm("ap-10", resource_type=ASSET_RESOURCE, resource_id="10")
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="root",
        protocol="ssh",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is perm


def test_decision_service_connect_deny_and_allow_with_trace() -> None:
    nodes = _tree()
    perm = _perm("ap-folder", resource_id="folder")
    service = PolicyDecisionService(
        asset_permissions=[perm],
        nodes=list(nodes.values()),
        asset_node_ids={"10": "leaf"},
    )
    denied = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="11", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            connector_trusted=True,
        )
    )
    allowed = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="10", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            connector_trusted=True,
        )
    )
    assert denied.decision is PolicyDecision.DENY
    assert denied.reason_code == "ASSET_PERMISSION_DENIED"
    assert allowed.decision is PolicyDecision.ALLOW
    assert allowed.reason_code == "ASSET_PERMISSION_ALLOWED"
    assert any("permission:ap-folder" in line for line in allowed.explain_trace)
    assert any("inherited:node:folder" in line for line in allowed.explain_trace)


def test_decision_service_does_not_bypass_for_admin_subject() -> None:
    nodes = _tree()
    service = PolicyDecisionService(
        asset_permissions=[],
        nodes=list(nodes.values()),
        asset_node_ids={"10": "leaf"},
    )
    result = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="admin", tenant_id="tenant-a"),
            resource=ResourceRef(id="10", type="asset", tenant_id="tenant-a"),
            action="connect",
            connector_trusted=True,
        )
    )
    assert result.decision is PolicyDecision.DENY
    assert result.reason_code == "ASSET_PERMISSION_DENIED"

def test_account_scoped_permission_visible_on_list_not_other_account() -> None:
    nodes = _tree()
    perm = _perm(
        "ap-root-only",
        resource_type=ASSET_RESOURCE,
        resource_id="10",
        account_id="root",
        protocol="ssh",
    )
    visible = connectable_asset_ids(
        subject_id="user-1",
        tenant_id="tenant-a",
        assets=[("10", "leaf")],
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert visible == {"10"}
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="admin",
        protocol="ssh",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is None
    matched, _path = find_effective_connect_permission(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        asset_node_id="leaf",
        account_id="root",
        protocol="ssh",
        permissions=[perm],
        nodes_by_id=nodes,
    )
    assert matched is perm


def test_unwired_service_keeps_policy_rule_path() -> None:
    service = PolicyDecisionService()
    result = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="10", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            connector_trusted=True,
        )
    )
    assert result.reason_code == "NO_MATCHING_POLICY"
