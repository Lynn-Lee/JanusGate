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
        "context": {"connector_id": "connector-1"},
        "mfa_verified": True,
        "approval": ApprovalState(status="approved", expires_at=datetime.now(UTC) + timedelta(minutes=10)),
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
