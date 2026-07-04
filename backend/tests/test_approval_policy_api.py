"""Phase 4 approval policy template API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.deps import current_user
from app.main import app


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def install_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db


def install_user(*, tenant_id: str, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.mark.asyncio
async def test_approval_policy_api_creates_and_lists_with_tenant_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:admin"])
        create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "approver_mode": "named_user",
                "require_mfa_for_requester": True,
                "require_mfa_for_approver": True,
                "max_grant_ttl_seconds": 900,
                "allow_self_approval": False,
                "risk_level": "high",
            },
        )
        tenant_a_list = client.get("/api/v1/workflows/approval-policies")

        install_user(tenant_id="tenant-b", permissions=["workflow:admin"])
        tenant_b_list = client.get("/api/v1/workflows/approval-policies")

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["resource_selector"] == {"asset_id": "asset-1", "protocol": "ssh"}
    assert created["action_selector"] == "session.connect"
    assert created["approver_subject_ids"] == ["manager-1"]
    assert created["approver_mode"] == "named_user"
    assert created["require_mfa_for_requester"] is True
    assert created["require_mfa_for_approver"] is True
    assert created["max_grant_ttl_seconds"] == 900
    assert created["allow_self_approval"] is False
    assert created["risk_level"] == "high"
    assert "dsl" not in created
    assert tenant_a_list.status_code == 200
    assert tenant_a_list.json() == {"items": [created], "total": 1}
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_approval_policy_api_requires_workflow_admin_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:approve"])
        response = client.get("/api/v1/workflows/approval-policies")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_approval_policy_simulation_evaluates_current_tenant_templates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:admin"])
        create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "require_mfa_for_requester": False,
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
            },
        )
        simulate_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh", "account_id": "account-1"},
                "connector_trusted": True,
            },
        )

        install_user(tenant_id="tenant-b", permissions=["workflow:admin"])
        cross_tenant_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-b"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-b"},
                "context": {"protocol": "ssh", "account_id": "account-1"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert simulate_response.status_code == 200
    simulated = simulate_response.json()
    assert simulated["decision"] == "deny"
    assert simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert simulated["obligations"]["approval_policy_id"] == created["id"]
    assert simulated["obligations"]["approver_subject_ids"] == ["manager-1"]
    assert simulated["obligations"]["risk_level"] == "high"
    assert simulated["ttl_seconds"] == 0
    assert "approval_policy:" in " ".join(simulated["explain_trace"])
    assert cross_tenant_response.status_code == 200
    assert cross_tenant_response.json()["reason_code"] == "NO_MATCHING_POLICY"


@pytest.mark.asyncio
async def test_approval_policy_rollout_percentage_can_exclude_simulation_subjects(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:admin"])
        create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
                "rollout_percentage": 0,
            },
        )
        simulate_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["rollout_percentage"] == 0
    assert simulate_response.status_code == 200
    simulated = simulate_response.json()
    assert simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:rollout_excluded" in simulated["explain_trace"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_equals_filters_simulation_requests(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:admin"])
        create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-1"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
                "dsl_conditions": {"context_equals": {"protocol": "rdp"}},
            },
        )
        ssh_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh"},
                "connector_trusted": True,
            },
        )
        rdp_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "rdp"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert ssh_response.status_code == 200
    ssh_simulated = ssh_response.json()
    assert ssh_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in ssh_simulated["explain_trace"]
    assert rdp_response.status_code == 200
    rdp_simulated = rdp_response.json()
    assert rdp_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert rdp_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_version_api_supersedes_current_tenant_policy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:admin"])
        create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
            },
        )
        created = create_response.json()
        version_response = client.post(
            f"/api/v1/workflows/approval-policies/{created['id']}/versions",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["director-1"],
                "max_grant_ttl_seconds": 600,
                "risk_level": "critical",
            },
        )
        latest_list = client.get("/api/v1/workflows/approval-policies")
        simulate_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh"},
                "connector_trusted": True,
            },
        )

        install_user(tenant_id="tenant-b", permissions=["workflow:admin"])
        cross_tenant_version_response = client.post(
            f"/api/v1/workflows/approval-policies/{created['id']}/versions",
            json={
                "resource_selector": {"asset_id": "asset-1"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["mallory"],
            },
        )

    assert create_response.status_code == 201
    assert created["policy_family_id"] == created["id"]
    assert created["version"] == 1
    assert created["is_active"] is True
    assert version_response.status_code == 201
    versioned = version_response.json()
    assert versioned["id"] != created["id"]
    assert versioned["policy_family_id"] == created["policy_family_id"]
    assert versioned["version"] == 2
    assert versioned["is_active"] is True
    assert versioned["approver_subject_ids"] == ["director-1"]
    assert latest_list.status_code == 200
    assert latest_list.json()["items"] == [versioned]
    assert simulate_response.status_code == 200
    simulated = simulate_response.json()
    assert simulated["obligations"]["approval_policy_id"] == versioned["id"]
    assert simulated["obligations"]["approver_subject_ids"] == ["director-1"]
    assert simulated["obligations"]["risk_level"] == "critical"
    assert cross_tenant_version_response.status_code == 404


@pytest.mark.asyncio
async def test_approval_policy_rollback_reactivates_selected_tenant_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["workflow:admin"])
        create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
            },
        )
        created = create_response.json()
        version_response = client.post(
            f"/api/v1/workflows/approval-policies/{created['id']}/versions",
            json={
                "resource_selector": {"asset_id": "asset-1", "protocol": "ssh"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["director-1"],
                "max_grant_ttl_seconds": 600,
                "risk_level": "critical",
            },
        )
        rollback_response = client.post(
            f"/api/v1/workflows/approval-policies/{created['id']}/rollback"
        )
        latest_list = client.get("/api/v1/workflows/approval-policies")
        simulate_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh"},
                "connector_trusted": True,
            },
        )

        install_user(tenant_id="tenant-b", permissions=["workflow:admin"])
        cross_tenant_rollback_response = client.post(
            f"/api/v1/workflows/approval-policies/{created['id']}/rollback"
        )

    assert create_response.status_code == 201
    assert version_response.status_code == 201
    assert rollback_response.status_code == 200
    rolled_back = rollback_response.json()
    assert rolled_back["id"] == created["id"]
    assert rolled_back["policy_family_id"] == created["policy_family_id"]
    assert rolled_back["version"] == 1
    assert rolled_back["is_active"] is True
    assert rolled_back["approver_subject_ids"] == ["manager-1"]
    assert latest_list.status_code == 200
    assert latest_list.json()["items"] == [rolled_back]
    assert simulate_response.status_code == 200
    simulated = simulate_response.json()
    assert simulated["obligations"]["approval_policy_id"] == created["id"]
    assert simulated["obligations"]["approver_subject_ids"] == ["manager-1"]
    assert simulated["obligations"]["risk_level"] == "high"
    assert cross_tenant_rollback_response.status_code == 404
