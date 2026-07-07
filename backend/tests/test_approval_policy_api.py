"""Phase 4 approval policy template API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
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
    app.dependency_overrides[get_read_db] = override_db


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
async def test_approval_policy_dsl_context_in_filters_simulation_requests(
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
                "dsl_conditions": {"context_in": {"protocol": ["ssh", "rdp"]}},
            },
        )
        database_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "database"},
                "connector_trusted": True,
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

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert database_response.status_code == 200
    database_simulated = database_response.json()
    assert database_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in database_simulated["explain_trace"]
    assert ssh_response.status_code == 200
    ssh_simulated = ssh_response.json()
    assert ssh_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert ssh_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_not_equals_filters_simulation_requests(
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
                "dsl_conditions": {"context_not_equals": {"account_tier": "sandbox"}},
            },
        )
        sandbox_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"account_tier": "sandbox"},
                "connector_trusted": True,
            },
        )
        production_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"account_tier": "production"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert sandbox_response.status_code == 200
    sandbox_simulated = sandbox_response.json()
    assert sandbox_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in sandbox_simulated["explain_trace"]
    assert production_response.status_code == 200
    production_simulated = production_response.json()
    assert production_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert production_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_not_in_filters_simulation_requests(
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
                "dsl_conditions": {"context_not_in": {"account_tier": ["sandbox", "break-glass"]}},
            },
        )
        sandbox_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"account_tier": "sandbox"},
                "connector_trusted": True,
            },
        )
        production_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"account_tier": "production"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert sandbox_response.status_code == 200
    sandbox_simulated = sandbox_response.json()
    assert sandbox_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in sandbox_simulated["explain_trace"]
    assert production_response.status_code == 200
    production_simulated = production_response.json()
    assert production_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert production_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_numeric_thresholds_filter_simulation_requests(
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
                "dsl_conditions": {
                    "context_number_gte": {"risk_score": 80},
                    "context_number_lte": {"session_duration_minutes": 60},
                },
            },
        )
        low_risk_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 79, "session_duration_minutes": 30},
                "connector_trusted": True,
            },
        )
        long_session_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 90, "session_duration_minutes": 61},
                "connector_trusted": True,
            },
        )
        invalid_numeric_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": "high", "session_duration_minutes": 30},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 80, "session_duration_minutes": "60"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert low_risk_response.status_code == 200
    low_risk_simulated = low_risk_response.json()
    assert low_risk_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in low_risk_simulated[
        "explain_trace"
    ]
    assert long_session_response.status_code == 200
    long_session_simulated = long_session_response.json()
    assert long_session_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in long_session_simulated[
        "explain_trace"
    ]
    assert invalid_numeric_response.status_code == 200
    invalid_numeric_simulated = invalid_numeric_response.json()
    assert invalid_numeric_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in invalid_numeric_simulated[
        "explain_trace"
    ]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_number_between_filters_simulation_requests(
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
                "dsl_conditions": {
                    "context_number_between": {
                        "risk_score": {"min": 70, "max": 90},
                        "session_duration_minutes": {"min": 15, "max": 60},
                    }
                },
            },
        )
        below_min_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 69.9, "session_duration_minutes": 30},
                "connector_trusted": True,
            },
        )
        above_max_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 80, "session_duration_minutes": 60.1},
                "connector_trusted": True,
            },
        )
        invalid_numeric_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": True, "session_duration_minutes": 30},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": "70", "session_duration_minutes": "60"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    for response in [below_min_response, above_max_response, invalid_numeric_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_number_not_between_filters_simulation_requests(
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
                "dsl_conditions": {
                    "context_number_not_between": {
                        "risk_score": {"min": 0, "max": 69},
                        "session_duration_minutes": {"min": 121, "max": 240},
                    }
                },
            },
        )
        low_risk_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 69, "session_duration_minutes": 60},
                "connector_trusted": True,
            },
        )
        long_session_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 80, "session_duration_minutes": 121},
                "connector_trusted": True,
            },
        )
        invalid_numeric_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": "high", "session_duration_minutes": 60},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": "70", "session_duration_minutes": "120"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    for response in [low_risk_response, long_session_response, invalid_numeric_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_strict_numeric_thresholds_filter_simulation_requests(
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
                "dsl_conditions": {
                    "context_number_gt": {"risk_score": 80},
                    "context_number_lt": {"session_duration_minutes": 60},
                },
            },
        )
        boundary_risk_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 80, "session_duration_minutes": 30},
                "connector_trusted": True,
            },
        )
        boundary_duration_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 81, "session_duration_minutes": 60},
                "connector_trusted": True,
            },
        )
        invalid_numeric_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": True, "session_duration_minutes": 30},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": "80.5", "session_duration_minutes": "59.5"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    for response in [boundary_risk_response, boundary_duration_response, invalid_numeric_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_exists_filters_simulation_requests(
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
                "dsl_conditions": {"context_exists": ["ticket_id", "risk_score"]},
            },
        )
        missing_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"risk_score": 90},
                "connector_trusted": True,
            },
        )
        null_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": None, "risk_score": 90},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123", "risk_score": 90},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert missing_ticket_response.status_code == 200
    missing_ticket_simulated = missing_ticket_response.json()
    assert missing_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in missing_ticket_simulated[
        "explain_trace"
    ]
    assert null_ticket_response.status_code == 200
    null_ticket_simulated = null_ticket_response.json()
    assert null_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in null_ticket_simulated[
        "explain_trace"
    ]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_contains_filters_simulation_requests(
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
                "dsl_conditions": {"context_contains": {"ticket_id": "CHG-"}},
            },
        )
        missing_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        numeric_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": 12345},
                "connector_trusted": True,
            },
        )
        incident_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "INC-123"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert missing_ticket_response.status_code == 200
    missing_ticket_simulated = missing_ticket_response.json()
    assert missing_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in missing_ticket_simulated[
        "explain_trace"
    ]
    assert numeric_ticket_response.status_code == 200
    numeric_ticket_simulated = numeric_ticket_response.json()
    assert numeric_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in numeric_ticket_simulated[
        "explain_trace"
    ]
    assert incident_ticket_response.status_code == 200
    incident_ticket_simulated = incident_ticket_response.json()
    assert incident_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in incident_ticket_simulated[
        "explain_trace"
    ]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_not_contains_filters_simulation_requests(
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
                "dsl_conditions": {"context_not_contains": {"ticket_id": "BREAKGLASS"}},
            },
        )
        missing_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        numeric_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": 12345},
                "connector_trusted": True,
            },
        )
        excluded_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123-BREAKGLASS"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123-STANDARD"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    for response in [missing_ticket_response, numeric_ticket_response, excluded_ticket_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_starts_with_filters_simulation_requests(
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
                "dsl_conditions": {"context_starts_with": {"ticket_id": "CHG-"}},
            },
        )
        missing_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        numeric_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": 12345},
                "connector_trusted": True,
            },
        )
        incident_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "INC-123"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert missing_ticket_response.status_code == 200
    missing_ticket_simulated = missing_ticket_response.json()
    assert missing_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in missing_ticket_simulated[
        "explain_trace"
    ]
    assert numeric_ticket_response.status_code == 200
    numeric_ticket_simulated = numeric_ticket_response.json()
    assert numeric_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in numeric_ticket_simulated[
        "explain_trace"
    ]
    assert incident_ticket_response.status_code == 200
    incident_ticket_simulated = incident_ticket_response.json()
    assert incident_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in incident_ticket_simulated[
        "explain_trace"
    ]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_ends_with_filters_simulation_requests(
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
                "dsl_conditions": {"context_ends_with": {"ticket_id": "-EMERGENCY"}},
            },
        )
        missing_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        numeric_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": 12345},
                "connector_trusted": True,
            },
        )
        standard_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123-STANDARD"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123-EMERGENCY"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    assert missing_ticket_response.status_code == 200
    missing_ticket_simulated = missing_ticket_response.json()
    assert missing_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in missing_ticket_simulated[
        "explain_trace"
    ]
    assert numeric_ticket_response.status_code == 200
    numeric_ticket_simulated = numeric_ticket_response.json()
    assert numeric_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in numeric_ticket_simulated[
        "explain_trace"
    ]
    assert standard_ticket_response.status_code == 200
    standard_ticket_simulated = standard_ticket_response.json()
    assert standard_ticket_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in standard_ticket_simulated[
        "explain_trace"
    ]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_matches_regex_filters_simulation_requests(
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
                "dsl_conditions": {"context_matches_regex": {"ticket_id": r"^CHG-\d{3}$"}},
            },
        )
        missing_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        numeric_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": 12345},
                "connector_trusted": True,
            },
        )
        incident_ticket_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "INC-123"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123"},
                "connector_trusted": True,
            },
        )

        invalid_create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-2"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
                "dsl_conditions": {"context_matches_regex": {"ticket_id": "["}},
            },
        )
        invalid_regex_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-2", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"ticket_id": "CHG-123"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert "dsl_conditions" not in created
    for response in [missing_ticket_response, numeric_ticket_response, incident_ticket_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]

    assert invalid_create_response.status_code == 201
    invalid_created = invalid_create_response.json()
    assert invalid_regex_response.status_code == 200
    invalid_regex_simulated = invalid_regex_response.json()
    assert invalid_regex_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{invalid_created['id']}:dsl_excluded" in invalid_regex_simulated[
        "explain_trace"
    ]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_ip_in_cidr_filters_simulation_requests(
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
                "dsl_conditions": {
                    "context_ip_in_cidr": {
                        "source_ip": ["203.0.113.0/24", "2001:db8:42::/48"]
                    }
                },
            },
        )
        missing_ip_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        invalid_ip_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"source_ip": "not-an-ip"},
                "connector_trusted": True,
            },
        )
        outside_cidr_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"source_ip": "198.51.100.10"},
                "connector_trusted": True,
            },
        )
        matching_ipv4_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"source_ip": "203.0.113.17"},
                "connector_trusted": True,
            },
        )
        matching_ipv6_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"source_ip": "2001:db8:42::5"},
                "connector_trusted": True,
            },
        )

        invalid_cidr_create_response = client.post(
            "/api/v1/workflows/approval-policies",
            json={
                "resource_selector": {"asset_id": "asset-2"},
                "action_selector": "session.connect",
                "approver_subject_ids": ["manager-1"],
                "max_grant_ttl_seconds": 900,
                "risk_level": "high",
                "dsl_conditions": {"context_ip_in_cidr": {"source_ip": ["bad-cidr"]}},
            },
        )
        invalid_cidr_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-2", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"source_ip": "203.0.113.17"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    for response in [missing_ip_response, invalid_ip_response, outside_cidr_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    for response in [matching_ipv4_response, matching_ipv6_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "APPROVAL_REQUIRED"
        assert simulated["obligations"]["approval_policy_id"] == created["id"]

    assert invalid_cidr_create_response.status_code == 201
    invalid_created = invalid_cidr_create_response.json()
    assert invalid_cidr_response.status_code == 200
    invalid_simulated = invalid_cidr_response.json()
    assert invalid_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{invalid_created['id']}:dsl_excluded" in invalid_simulated[
        "explain_trace"
    ]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_time_between_filters_simulation_requests(
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
                "dsl_conditions": {
                    "context_time_between": {
                        "request_time": {"start": "09:00", "end": "17:30"}
                    }
                },
            },
        )
        missing_time_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        invalid_time_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"request_time": "tomorrow morning"},
                "connector_trusted": True,
            },
        )
        outside_window_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"request_time": "18:00"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"request_time": "2026-07-07T10:15:00Z"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    for response in [missing_time_response, invalid_time_response, outside_window_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_context_time_not_between_filters_simulation_requests(
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
                "dsl_conditions": {
                    "context_time_not_between": {
                        "request_time": {"start": "09:00", "end": "17:30"}
                    }
                },
            },
        )
        missing_time_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {},
                "connector_trusted": True,
            },
        )
        invalid_time_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"request_time": "tomorrow morning"},
                "connector_trusted": True,
            },
        )
        inside_window_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"request_time": "10:15"},
                "connector_trusted": True,
            },
        )
        matching_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"request_time": "2026-07-07T18:00:00Z"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    for response in [missing_time_response, invalid_time_response, inside_window_response]:
        assert response.status_code == 200
        simulated = response.json()
        assert simulated["reason_code"] == "NO_MATCHING_POLICY"
        assert f"approval_policy:{created['id']}:dsl_excluded" in simulated["explain_trace"]
    assert matching_response.status_code == 200
    matching_simulated = matching_response.json()
    assert matching_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert matching_simulated["obligations"]["approval_policy_id"] == created["id"]


@pytest.mark.asyncio
async def test_approval_policy_dsl_any_all_composes_simulation_conditions(
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
                "dsl_conditions": {
                    "any": [
                        {
                            "all": [
                                {"context_equals": {"protocol": "ssh"}},
                                {"context_in": {"risk_level": ["high", "critical"]}},
                            ]
                        },
                        {"context_equals": {"break_glass": "true"}},
                    ]
                },
            },
        )
        low_risk_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh", "risk_level": "low"},
                "connector_trusted": True,
            },
        )
        high_risk_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "ssh", "risk_level": "high"},
                "connector_trusted": True,
            },
        )
        break_glass_response = client.post(
            "/api/v1/workflows/approval-policies/simulate",
            json={
                "subject": {"id": "user-2", "tenant_id": "tenant-a"},
                "action": "session.connect",
                "resource": {"id": "asset-1", "type": "asset", "tenant_id": "tenant-a"},
                "context": {"protocol": "rdp", "break_glass": "true"},
                "connector_trusted": True,
            },
        )

    assert create_response.status_code == 201
    created = create_response.json()
    assert low_risk_response.status_code == 200
    low_risk_simulated = low_risk_response.json()
    assert low_risk_simulated["reason_code"] == "NO_MATCHING_POLICY"
    assert f"approval_policy:{created['id']}:dsl_excluded" in low_risk_simulated[
        "explain_trace"
    ]
    assert high_risk_response.status_code == 200
    high_risk_simulated = high_risk_response.json()
    assert high_risk_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert high_risk_simulated["obligations"]["approval_policy_id"] == created["id"]
    assert break_glass_response.status_code == 200
    break_glass_simulated = break_glass_response.json()
    assert break_glass_simulated["reason_code"] == "APPROVAL_REQUIRED"
    assert break_glass_simulated["obligations"]["approval_policy_id"] == created["id"]


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
