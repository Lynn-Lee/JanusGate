"""#t65 overlay ACL 判定：登录 / 资产登录 / 连接方式（内存模型，不落库）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.acl import (
    ConnectMethodAclModel,
    LoginAclModel,
    LoginAssetAclModel,
    OverlayAclAction,
)
from app.models.asset_tree import (
    ASSET_RESOURCE,
    CONNECT_ACTION,
    AssetPermissionModel,
    NodeModel,
)
from app.models.tenancy import Tenant
from app.policy.decision import PolicyDecisionService
from app.policy.repository import build_tenant_policy_service
from app.policy.schemas import PolicyDecision, PolicyDecisionRequest, ResourceRef, SubjectRef
from app.tenancy.scope import ActorScope


def _login_acl(
    acl_id: str,
    *,
    subject_id: str = "1",
    action: OverlayAclAction = OverlayAclAction.REJECT,
    priority: int = 50,
    tenant_id: str = "tenant-a",
    name: str | None = None,
) -> LoginAclModel:
    return LoginAclModel(
        id=acl_id,
        tenant_id=tenant_id,
        name=name or acl_id,
        priority=priority,
        action=action,
        subject_id=subject_id,
    )


def _login_asset_acl(
    acl_id: str,
    *,
    resource_type: str = "asset",
    resource_id: str = "10",
    action: OverlayAclAction = OverlayAclAction.REJECT,
    priority: int = 50,
    tenant_id: str = "tenant-a",
    ip_cidr: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
) -> LoginAssetAclModel:
    return LoginAssetAclModel(
        id=acl_id,
        tenant_id=tenant_id,
        name=acl_id,
        priority=priority,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_cidr=ip_cidr,
        time_start=time_start,
        time_end=time_end,
    )


def _connect_method_acl(
    acl_id: str,
    *,
    protocol: str = "ssh",
    resource_type: str | None = "asset",
    resource_id: str | None = "10",
    action: OverlayAclAction = OverlayAclAction.REJECT,
    priority: int = 50,
    tenant_id: str = "tenant-a",
) -> ConnectMethodAclModel:
    return ConnectMethodAclModel(
        id=acl_id,
        tenant_id=tenant_id,
        name=acl_id,
        priority=priority,
        action=action,
        protocol=protocol,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _perm(*, asset_id: str = "10") -> AssetPermissionModel:
    return AssetPermissionModel(
        id=f"ap-{asset_id}",
        tenant_id="tenant-a",
        subject_id="user-1",
        subject_type="user",
        resource_type=ASSET_RESOURCE,
        resource_id=asset_id,
        account_id="",
        protocol="",
        action=CONNECT_ACTION,
        expires_at=None,
    )


def _node(
    node_id: str,
    *,
    parent_id: str | None,
    ancestor_ids: list[str],
) -> NodeModel:
    return NodeModel(
        id=node_id,
        tenant_id="tenant-a",
        parent_id=parent_id,
        name=node_id,
        ancestor_ids_json=json.dumps(ancestor_ids),
    )


def _connect_request(
    *,
    asset_id: str = "10",
    protocol: str = "ssh",
    client_ip: str = "10.0.0.8",
    now: datetime | None = None,
    subject_id: str = "user-1",
) -> PolicyDecisionRequest:
    context: dict[str, object] = {
        "account_id": "root",
        "protocol": protocol,
        "client_ip": client_ip,
    }
    if now is not None:
        context["now"] = now
    return PolicyDecisionRequest(
        subject=SubjectRef(id=subject_id, type="user", tenant_id="tenant-a"),
        action="session.connect",
        resource=ResourceRef(id=asset_id, type="asset", tenant_id="tenant-a"),
        context=context,
        connector_trusted=True,
    )


def test_login_acl_no_rules_allows() -> None:
    result = PolicyDecisionService().evaluate_login("1", "tenant-a")
    assert result.decision == PolicyDecision.ALLOW


def test_login_acl_reject_for_subject() -> None:
    service = PolicyDecisionService(login_acls=[_login_acl("la-1", subject_id="1")])
    denied = service.evaluate_login("1", "tenant-a")
    allowed = service.evaluate_login("2", "tenant-a")
    assert denied.decision == PolicyDecision.DENY
    assert denied.reason_code == "LOGIN_ACL_REJECTED"
    assert allowed.decision == PolicyDecision.ALLOW


def test_login_acl_accept_for_subject() -> None:
    service = PolicyDecisionService(
        login_acls=[_login_acl("la-1", subject_id="1", action=OverlayAclAction.ACCEPT)]
    )
    result = service.evaluate_login("1", "tenant-a")
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "LOGIN_ACL_ACCEPTED"


def test_login_acl_does_not_bypass_admin() -> None:
    service = PolicyDecisionService(login_acls=[_login_acl("la-admin", subject_id="admin-1")])
    result = service.evaluate_login("admin-1", "tenant-a")
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ACL_REJECTED"


def test_login_asset_empty_cidr_matches_all_and_reject() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-1")],
    )
    result = service.evaluate(_connect_request(client_ip="1.2.3.4"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ASSET_ACL_REJECTED"


def test_login_asset_non_matching_cidr_falls_through() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-1", ip_cidr="10.0.0.0/8")],
    )
    result = service.evaluate(_connect_request(client_ip="1.2.3.4"))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "ASSET_PERMISSION_ALLOWED"


def test_login_asset_matching_cidr_reject() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-1", ip_cidr="10.0.0.0/8")],
    )
    result = service.evaluate(_connect_request(client_ip="10.1.2.3"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ASSET_ACL_REJECTED"


def test_login_asset_invalid_cidr_does_not_match() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-1", ip_cidr="not-a-cidr")],
    )
    result = service.evaluate(_connect_request())
    assert result.decision == PolicyDecision.ALLOW


def test_login_asset_node_covers_descendant() -> None:
    nodes = [
        _node("root", parent_id=None, ancestor_ids=[]),
        _node("folder", parent_id="root", ancestor_ids=["root"]),
        _node("leaf", parent_id="folder", ancestor_ids=["root", "folder"]),
    ]
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        nodes=nodes,
        asset_node_ids={"10": "leaf"},
        login_asset_acls=[_login_asset_acl("laa-node", resource_type="node", resource_id="folder")],
    )
    result = service.evaluate(_connect_request())
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ASSET_ACL_REJECTED"


def test_login_asset_ungrouped_not_covered_by_node_rule() -> None:
    nodes = [
        _node("root", parent_id=None, ancestor_ids=[]),
        _node("folder", parent_id="root", ancestor_ids=["root"]),
    ]
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        nodes=nodes,
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-node", resource_type="node", resource_id="folder")],
    )
    result = service.evaluate(_connect_request())
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "ASSET_PERMISSION_ALLOWED"


def test_login_asset_time_window_outside_allows() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[
            _login_asset_acl("laa-1", time_start="09:00", time_end="18:00")
        ],
    )
    # Default tenant TZ is Asia/Singapore: 00:00 UTC = 08:00 SGT, outside 09:00-18:00.
    result = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 0, 0, tzinfo=UTC)))
    assert result.decision == PolicyDecision.ALLOW


def test_login_asset_time_window_inside_rejects() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[
            _login_asset_acl("laa-1", time_start="09:00", time_end="18:00")
        ],
    )
    # Default tenant TZ is Asia/Singapore: 01:00 UTC = 09:00 SGT, inside 09:00-18:00.
    result = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 1, 0, tzinfo=UTC)))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ASSET_ACL_REJECTED"


def test_login_asset_wrap_midnight_is_invalid_no_match() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[
            _login_asset_acl("laa-1", time_start="22:00", time_end="06:00")
        ],
    )
    result = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 23, 0, tzinfo=UTC)))
    assert result.decision == PolicyDecision.ALLOW


def test_connect_method_rejects_matching_protocol() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        connect_method_acls=[_connect_method_acl("cma-1", protocol="ssh")],
    )
    denied = service.evaluate(_connect_request(protocol="ssh"))
    allowed = service.evaluate(_connect_request(protocol="k8s"))
    assert denied.decision == PolicyDecision.DENY
    assert denied.reason_code == "CONNECT_METHOD_ACL_REJECTED"
    assert allowed.decision == PolicyDecision.ALLOW


def test_connect_method_empty_apply_to_rejects_all_assets() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm(asset_id="99")],
        asset_node_ids={"99": "leaf"},
        connect_method_acls=[
            _connect_method_acl(
                "cma-all", protocol="ssh", resource_type=None, resource_id=None
            )
        ],
    )
    result = service.evaluate(_connect_request(asset_id="99"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "CONNECT_METHOD_ACL_REJECTED"


def test_connect_method_has_no_user_selector() -> None:
    service = PolicyDecisionService(
        asset_permissions=[
            AssetPermissionModel(
                id="ap-admin",
                tenant_id="tenant-a",
                subject_id="admin-1",
                subject_type="user",
                resource_type=ASSET_RESOURCE,
                resource_id="10",
                account_id="",
                protocol="",
                action=CONNECT_ACTION,
                expires_at=None,
            )
        ],
        asset_node_ids={"10": None},
        connect_method_acls=[_connect_method_acl("cma-1")],
    )
    result = service.evaluate(_connect_request(subject_id="admin-1"))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "CONNECT_METHOD_ACL_REJECTED"


def test_overlay_not_consulted_when_asset_permission_denies() -> None:
    service = PolicyDecisionService(
        asset_permissions=[],
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-1")],
        connect_method_acls=[_connect_method_acl("cma-1")],
    )
    result = service.evaluate(_connect_request())
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "ASSET_PERMISSION_DENIED"
    assert all("login_asset_acl" not in item for item in result.explain_trace)
    assert all("connect_method_acl" not in item for item in result.explain_trace)


def test_overlay_reason_after_asset_permission_allow() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[_login_asset_acl("laa-1")],
    )
    result = service.evaluate(_connect_request())
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ASSET_ACL_REJECTED"


def test_login_asset_sgt_window_matches_at_0100_utc() -> None:
    """01:00 UTC = 09:00 SGT; stored 09:00-18:00 must match (not evaluated as UTC)."""

    acl = _login_asset_acl("laa-1", time_start="09:00", time_end="18:00")
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[acl],
        tenant_timezone="Asia/Singapore",
    )
    result = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 1, 0, tzinfo=UTC)))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "LOGIN_ASSET_ACL_REJECTED"
    assert acl.time_start == "09:00"
    assert acl.time_end == "18:00"


def test_login_asset_sgt_window_does_not_match_at_0000_utc() -> None:
    """00:00 UTC = 08:00 SGT; stored 09:00-18:00 must not match."""

    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[
            _login_asset_acl("laa-1", time_start="09:00", time_end="18:00")
        ],
        tenant_timezone="Asia/Singapore",
    )
    result = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 0, 0, tzinfo=UTC)))
    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "ASSET_PERMISSION_ALLOWED"


def test_login_asset_time_follows_current_tenant_timezone_not_snapshot() -> None:
    """Same stored HH:MM is reinterpreted if the tenant timezone later changes."""

    acl = _login_asset_acl("laa-1", time_start="09:00", time_end="18:00")
    now = datetime(2026, 9, 2, 1, 0, tzinfo=UTC)
    shared = {
        "asset_permissions": [_perm()],
        "asset_node_ids": {"10": None},
        "login_asset_acls": [acl],
    }
    sgt = PolicyDecisionService(**shared, tenant_timezone="Asia/Singapore")
    utc = PolicyDecisionService(**shared, tenant_timezone="UTC")
    assert sgt.evaluate(_connect_request(now=now)).decision == PolicyDecision.DENY
    assert utc.evaluate(_connect_request(now=now)).decision == PolicyDecision.ALLOW
    assert acl.time_start == "09:00"
    assert acl.time_end == "18:00"


def test_invalid_tenant_timezone_fails_closed_to_singapore() -> None:
    service = PolicyDecisionService(
        asset_permissions=[_perm()],
        asset_node_ids={"10": None},
        login_asset_acls=[
            _login_asset_acl("laa-1", time_start="09:00", time_end="18:00")
        ],
        tenant_timezone="Not/AZone",
    )
    matched = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 1, 0, tzinfo=UTC)))
    missed = service.evaluate(_connect_request(now=datetime(2026, 9, 2, 0, 0, tzinfo=UTC)))
    assert matched.decision == PolicyDecision.DENY
    assert missed.decision == PolicyDecision.ALLOW

@pytest.mark.asyncio
async def test_build_tenant_policy_service_uses_live_tenant_timezone() -> None:
    """Changing Tenant.timezone reinterprets the same stored HH:MM; ACL row is not snapshotted."""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(Tenant(id="tenant-a", timezone="Asia/Singapore"))
            session.add(_perm())
            session.add(_login_asset_acl("laa-1", time_start="09:00", time_end="18:00"))
            await session.commit()
            scope = ActorScope(user_id="user-1", tenant_id="tenant-a")
            now = datetime(2026, 9, 2, 1, 0, tzinfo=UTC)
            denied = (await build_tenant_policy_service(session, scope)).evaluate(
                _connect_request(now=now)
            )
            assert denied.decision == PolicyDecision.DENY
            assert denied.reason_code == "LOGIN_ASSET_ACL_REJECTED"

            tenant = await session.get(Tenant, "tenant-a")
            assert tenant is not None
            tenant.timezone = "UTC"
            await session.commit()

            allowed = (await build_tenant_policy_service(session, scope)).evaluate(
                _connect_request(now=now)
            )
            assert allowed.decision == PolicyDecision.ALLOW
            assert allowed.reason_code == "ASSET_PERMISSION_ALLOWED"

            acl = await session.get(LoginAssetAclModel, "laa-1")
            assert acl is not None
            assert acl.time_start == "09:00"
            assert acl.time_end == "18:00"
    finally:
        await engine.dispose()

