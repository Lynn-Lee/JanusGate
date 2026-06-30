from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.registry import (
    ConnectorEnrollmentToken,
    ConnectorRegistry,
    InMemoryConnectorStore,
)
from app.connectors.schemas import (
    ConnectorCapability,
    ConnectorRegistrationRequest,
    ConnectorStatus,
)
from app.policy.schemas import PolicyDecision, PolicyDecisionResponse


class StubPolicyService:
    def __init__(self, decision: PolicyDecision):
        self.decision = decision

    def evaluate(self, request):
        return PolicyDecisionResponse(
            decision=self.decision,
            reason_code="TEST_DECISION",
            explain_trace=["stub policy decision"],
            obligations={"max_session_ttl_seconds": 120},
            ttl_seconds=120,
            audit_event_id="pde_test",
        )


def test_connector_registration_rejects_invalid_enrollment_token():
    registry = ConnectorRegistry(store=InMemoryConnectorStore(), enrollment_tokens={"token-1"})

    with pytest.raises(ValueError, match="INVALID_ENROLLMENT_TOKEN"):
        registry.register(
            ConnectorRegistrationRequest(
                name="koko-1",
                environment="prod",
                public_key_fingerprint="sha256:abc",
                capabilities=[ConnectorCapability.SSH],
                enrollment_token="wrong-token",
            )
        )


def test_inactive_connector_cannot_request_connection_token():
    store = InMemoryConnectorStore()
    registry = ConnectorRegistry(store=store, enrollment_tokens={"token-1"})
    connector = registry.register(
        ConnectorRegistrationRequest(
            name="koko-1",
            environment="prod",
            public_key_fingerprint="sha256:abc",
            capabilities=[ConnectorCapability.SSH],
            enrollment_token="token-1",
        )
    )
    store.set_status(connector.id, ConnectorStatus.INACTIVE)

    with pytest.raises(ValueError, match="CONNECTOR_NOT_ACTIVE"):
        registry.issue_connection_token(
            connector.id,
            request={"action": "asset.connect"},
            policy_service=StubPolicyService(PolicyDecision.ALLOW),
        )


def test_denied_policy_blocks_connection_token():
    registry = ConnectorRegistry(store=InMemoryConnectorStore(), enrollment_tokens={"token-1"})
    connector = registry.register(
        ConnectorRegistrationRequest(
            name="koko-1",
            environment="prod",
            public_key_fingerprint="sha256:abc",
            capabilities=[ConnectorCapability.SSH],
            enrollment_token="token-1",
        )
    )

    with pytest.raises(ValueError, match="POLICY_DENIED"):
        registry.issue_connection_token(
            connector.id,
            request={"action": "asset.connect"},
            policy_service=StubPolicyService(PolicyDecision.DENY),
        )


def test_allowed_policy_returns_short_lived_connection_token():
    registry = ConnectorRegistry(store=InMemoryConnectorStore(), enrollment_tokens={"token-1"})
    connector = registry.register(
        ConnectorRegistrationRequest(
            name="koko-1",
            environment="prod",
            public_key_fingerprint="sha256:abc",
            capabilities=[ConnectorCapability.SSH],
            enrollment_token="token-1",
        )
    )

    token = registry.issue_connection_token(
        connector.id,
        request={"action": "asset.connect"},
        policy_service=StubPolicyService(PolicyDecision.ALLOW),
    )

    assert token.connector_id == connector.id
    assert token.token.startswith("jgt_")
    assert token.ttl_seconds == 120
    assert token.policy_audit_event_id == "pde_test"


def test_enrollment_token_cannot_be_reused_after_successful_registration():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens={
            "token-1": ConnectorEnrollmentToken(
                value="token-1",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        },
    )
    request = ConnectorRegistrationRequest(
        name="koko-1",
        environment="prod",
        public_key_fingerprint="sha256:abc",
        capabilities=[ConnectorCapability.SSH],
        enrollment_token="token-1",
    )

    registry.register(request)

    with pytest.raises(ValueError, match="ENROLLMENT_TOKEN_ALREADY_USED"):
        registry.register(request)


def test_expired_enrollment_token_is_rejected():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens={
            "token-1": ConnectorEnrollmentToken(
                value="token-1",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        },
    )

    with pytest.raises(ValueError, match="ENROLLMENT_TOKEN_EXPIRED"):
        registry.register(
            ConnectorRegistrationRequest(
                name="koko-1",
                environment="prod",
                public_key_fingerprint="sha256:abc",
                capabilities=[ConnectorCapability.SSH],
                enrollment_token="token-1",
            )
        )

def test_enrollment_token_bound_to_fingerprint_rejects_mismatch():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens={
            "token-1": ConnectorEnrollmentToken(
                value="token-1",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                public_key_fingerprint="sha256:expected",
            )
        },
    )

    with pytest.raises(ValueError, match="ENROLLMENT_TOKEN_BINDING_MISMATCH"):
        registry.register(
            ConnectorRegistrationRequest(
                name="koko-1",
                environment="prod",
                public_key_fingerprint="sha256:actual",
                capabilities=[ConnectorCapability.SSH],
                enrollment_token="token-1",
            )
        )
