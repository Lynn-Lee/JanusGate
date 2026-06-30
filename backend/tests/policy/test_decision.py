from datetime import UTC, datetime, timedelta

from app.policy.decision import PolicyDecisionService
from app.policy.schemas import (
    ApprovalState,
    PolicyDecision,
    PolicyDecisionRequest,
    PolicyRule,
    ResourceRef,
    SubjectRef,
)


def _request(**overrides):
    data = {
        "subject": SubjectRef(id="user-1", type="user", tenant_id="tenant-a"),
        "action": "asset.connect",
        "resource": ResourceRef(id="asset-1", type="ssh_asset", tenant_id="tenant-a"),
        "context": {"connector_id": "connector-1", "account_id": "root", "protocol": "ssh"},
        "mfa_verified": True,
        "approval": ApprovalState(
            status="approved", expires_at=datetime.now(UTC) + timedelta(minutes=10)
        ),
        "connector_trusted": True,
    }
    data.update(overrides)
    return PolicyDecisionRequest(**data)


def test_policy_denies_by_default_with_explain_trace():
    service = PolicyDecisionService(rules=[])

    result = service.evaluate(_request())

    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "NO_MATCHING_POLICY"
    assert result.explain_trace
    assert result.audit_event_id.startswith("pde_")


def test_policy_allows_explicit_matching_rule():
    service = PolicyDecisionService(
        rules=[
            PolicyRule(
                id="rule-1",
                subject_ids=["user-1"],
                actions=["asset.connect"],
                resource_ids=["asset-1"],
                tenant_id="tenant-a",
                require_mfa=True,
                require_approval=True,
            )
        ]
    )

    result = service.evaluate(_request())

    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "POLICY_ALLOWED"
    assert result.obligations["max_session_ttl_seconds"] == 900


def test_policy_denies_when_required_mfa_missing():
    service = PolicyDecisionService(
        rules=[
            PolicyRule(
                id="rule-1",
                subject_ids=["user-1"],
                actions=["asset.connect"],
                resource_ids=["asset-1"],
                tenant_id="tenant-a",
                require_mfa=True,
            )
        ]
    )

    result = service.evaluate(_request(mfa_verified=False))

    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "MFA_REQUIRED"


def test_policy_denies_expired_approval():
    service = PolicyDecisionService(
        rules=[
            PolicyRule(
                id="rule-1",
                subject_ids=["user-1"],
                actions=["asset.connect"],
                resource_ids=["asset-1"],
                tenant_id="tenant-a",
                require_approval=True,
            )
        ]
    )

    result = service.evaluate(
        _request(
            approval=ApprovalState(
                status="approved",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    )

    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "APPROVAL_EXPIRED"


def test_policy_approval_required_returns_workflow_obligation_without_grant():
    service = PolicyDecisionService(
        rules=[
            PolicyRule(
                id="rule-approval",
                subject_ids=["user-1"],
                actions=["session.connect"],
                resource_ids=["asset-1"],
                tenant_id="tenant-a",
                require_approval=True,
                approval_use_type="single-use",
                approval_max_uses=1,
            )
        ]
    )

    result = service.evaluate(_request(action="session.connect", approval=None))

    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "APPROVAL_REQUIRED"
    assert result.obligations["workflow_required"] is True
    assert result.obligations["approval_use_type"] == "single-use"
    assert result.obligations["approval_max_uses"] == 1


def test_policy_denies_approval_when_grant_constraints_do_not_match_resource():
    service = PolicyDecisionService(
        rules=[
            PolicyRule(
                id="rule-approval",
                subject_ids=["user-1"],
                actions=["session.connect"],
                resource_ids=["asset-1"],
                tenant_id="tenant-a",
                require_approval=True,
            )
        ]
    )

    result = service.evaluate(
        _request(
            action="session.connect",
            approval=ApprovalState(
                status="approved",
                grant_id="grant-1",
                workflow_request_id="wr-1",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                constraints={
                    "subject_id": "user-1",
                    "asset_id": "asset-2",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                },
            ),
        )
    )

    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "APPROVAL_CONSTRAINT_MISMATCH"


def test_policy_allows_valid_grant_and_returns_grant_obligations():
    service = PolicyDecisionService(
        rules=[
            PolicyRule(
                id="rule-approval",
                subject_ids=["user-1"],
                actions=["session.connect"],
                resource_ids=["asset-1"],
                tenant_id="tenant-a",
                require_approval=True,
                approval_use_type="limited-use",
                approval_max_uses=3,
            )
        ]
    )

    result = service.evaluate(
        _request(
            action="session.connect",
            approval=ApprovalState(
                status="approved",
                grant_id="grant-1",
                workflow_request_id="wr-1",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                constraints={
                    "subject_id": "user-1",
                    "asset_id": "asset-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "use_type": "limited-use",
                    "max_uses": 3,
                },
            ),
        )
    )

    assert result.decision == PolicyDecision.ALLOW
    assert result.reason_code == "POLICY_ALLOWED"
    assert result.obligations["jit_grant_id"] == "grant-1"
    assert result.obligations["workflow_request_id"] == "wr-1"
    assert result.obligations["approval_use_type"] == "limited-use"
    assert result.obligations["approval_max_uses"] == 3
