from datetime import UTC, datetime, timedelta

import pytest

from app.models.workflow import (
    ApprovalPolicyModel,
    JitGrantModel,
    WorkflowRequestModel,
    WorkflowRequestStatus,
)
from app.workflows.repository import InMemoryWorkflowRepository


def test_workflow_models_cover_phase2_persistence_fields():
    workflow_columns = WorkflowRequestModel.__table__.columns
    grant_columns = JitGrantModel.__table__.columns
    policy_columns = ApprovalPolicyModel.__table__.columns

    for field in (
        "tenant_id",
        "requester_id",
        "requester_username",
        "resource_type",
        "asset_id",
        "account_id",
        "protocol",
        "action",
        "reason",
        "requested_ttl_seconds",
        "status",
        "metadata_json",
        "submitted_at",
        "decided_at",
        "expires_at",
        "revoked_at",
        "decision_reason",
        "approver_id",
        "approver_username",
    ):
        assert field in workflow_columns

    for field in (
        "tenant_id",
        "workflow_request_id",
        "subject_id",
        "asset_id",
        "account_id",
        "protocol",
        "action",
        "status",
        "issued_at",
        "expires_at",
        "revoked_at",
        "max_session_ttl_seconds",
        "constraints_json",
    ):
        assert field in grant_columns

    for field in (
        "tenant_id",
        "resource_selector_json",
        "action_selector",
        "approver_subject_ids_json",
        "approver_mode",
        "require_mfa_for_requester",
        "require_mfa_for_approver",
        "max_grant_ttl_seconds",
        "allow_self_approval",
        "risk_level",
    ):
        assert field in policy_columns


def test_repository_creates_submits_and_queries_workflow_request():
    repo = InMemoryWorkflowRepository()

    request = repo.create_request(
        tenant_id="tenant-a",
        requester_id="user-1",
        requester_username="alice",
        resource_type="asset",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="incident response",
        requested_ttl_seconds=1800,
        metadata={"ticket_id": "INC-1"},
    )

    assert request.id.startswith("wr_")
    assert request.status == WorkflowRequestStatus.draft
    assert repo.get_request(request.id, tenant_id="tenant-a") == request

    submitted = repo.submit_request(request.id, tenant_id="tenant-a")
    assert submitted.status == WorkflowRequestStatus.pending
    assert submitted.submitted_at is not None

    assert repo.list_requests(tenant_id="tenant-a", requester_id="user-1") == [submitted]
    assert repo.list_requests(tenant_id="tenant-b") == []


def test_repository_approves_request_and_creates_active_grant():
    repo = InMemoryWorkflowRepository()
    request = repo.create_request(
        tenant_id="tenant-a",
        requester_id="user-1",
        requester_username="alice",
        resource_type="asset",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="incident response",
        requested_ttl_seconds=1800,
        metadata={},
    )
    repo.submit_request(request.id, tenant_id="tenant-a")

    grant = repo.approve_request(
        request.id,
        tenant_id="tenant-a",
        approver_id="manager-1",
        approver_username="manager",
        decision_reason="approved for incident",
        grant_ttl_seconds=900,
        max_session_ttl_seconds=600,
        constraints={"usage": "single-use"},
    )

    approved = repo.get_request(request.id, tenant_id="tenant-a")
    assert approved is not None
    assert approved.status == WorkflowRequestStatus.approved
    assert approved.approver_id == "manager-1"
    assert grant.id.startswith("jg_")
    assert grant.workflow_request_id == request.id
    assert grant.subject_id == "user-1"
    assert grant.expires_at > datetime.now(UTC)
    assert repo.find_active_grant(
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        now=datetime.now(UTC),
    ) == grant


def test_repository_rejects_invalid_transitions_and_expired_grants():
    repo = InMemoryWorkflowRepository()
    request = repo.create_request(
        tenant_id="tenant-a",
        requester_id="user-1",
        requester_username="alice",
        resource_type="asset",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="incident response",
        requested_ttl_seconds=1800,
        metadata={},
    )

    with pytest.raises(ValueError, match="pending"):
        repo.approve_request(
            request.id,
            tenant_id="tenant-a",
            approver_id="manager-1",
            approver_username="manager",
            decision_reason="approved",
            grant_ttl_seconds=900,
            max_session_ttl_seconds=600,
            constraints={},
        )

    repo.submit_request(request.id, tenant_id="tenant-a")
    grant = repo.approve_request(
        request.id,
        tenant_id="tenant-a",
        approver_id="manager-1",
        approver_username="manager",
        decision_reason="approved",
        grant_ttl_seconds=900,
        max_session_ttl_seconds=600,
        constraints={},
    )
    grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert repo.find_active_grant(
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        now=datetime.now(UTC),
    ) is None
