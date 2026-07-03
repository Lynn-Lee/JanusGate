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


def _enrollment_tokens(*tokens: ConnectorEnrollmentToken) -> dict[str, ConnectorEnrollmentToken]:
    return {token.token_digest: token for token in tokens}


def _active_token(plaintext: str = "token-1", **kwargs) -> ConnectorEnrollmentToken:
    return ConnectorEnrollmentToken.from_plaintext(
        plaintext,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        **kwargs,
    )


def _registration_request(enrollment_token: str = "token-1") -> ConnectorRegistrationRequest:
    return ConnectorRegistrationRequest(
        name="koko-1",
        environment="prod",
        public_key_fingerprint="sha256:abc",
        mtls_certificate_fingerprint="sha256:cert-abc",
        capabilities=[ConnectorCapability.SSH],
        enrollment_token=enrollment_token,
    )


def test_connector_registration_rejects_invalid_enrollment_token():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(_active_token()),
    )

    with pytest.raises(ValueError, match="INVALID_ENROLLMENT_TOKEN"):
        registry.register(_registration_request(enrollment_token="wrong-token"))


def test_inactive_connector_cannot_request_connection_token():
    store = InMemoryConnectorStore()
    registry = ConnectorRegistry(store=store, enrollment_tokens=_enrollment_tokens(_active_token()))
    connector = registry.register(_registration_request())
    store.set_status(connector.id, ConnectorStatus.INACTIVE)

    with pytest.raises(ValueError, match="CONNECTOR_NOT_ACTIVE"):
        registry.issue_connection_token(
            connector.id,
            request={"action": "asset.connect"},
            policy_service=StubPolicyService(PolicyDecision.ALLOW),
        )


def test_denied_policy_blocks_connection_token():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(_active_token()),
    )
    connector = registry.register(_registration_request())

    with pytest.raises(ValueError, match="POLICY_DENIED"):
        registry.issue_connection_token(
            connector.id,
            request={"action": "asset.connect"},
            policy_service=StubPolicyService(PolicyDecision.DENY),
        )


def test_allowed_policy_returns_short_lived_connection_token():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(_active_token()),
    )
    connector = registry.register(_registration_request())

    token = registry.issue_connection_token(
        connector.id,
        request={"action": "asset.connect"},
        policy_service=StubPolicyService(PolicyDecision.ALLOW),
    )

    assert token.connector_id == connector.id
    assert token.token.startswith("jgt_")
    assert token.ttl_seconds == 120
    assert token.policy_audit_event_id == "pde_test"


def test_connector_registration_records_mtls_certificate_fingerprint():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(
            _active_token(mtls_certificate_fingerprint="sha256:cert-abc")
        ),
    )

    connector = registry.register(_registration_request())

    assert connector.mtls_certificate_fingerprint == "sha256:cert-abc"


def test_attestation_required_enrollment_token_rejects_missing_claims():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(
            _active_token(attestation_nonce="nonce-1", attestation_digest="sha256:attest-abc")
        ),
    )

    with pytest.raises(ValueError, match="CONNECTOR_ATTESTATION_REQUIRED"):
        registry.register(_registration_request())


def test_connector_registration_records_matching_attestation_claims():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(
            _active_token(attestation_nonce="nonce-1", attestation_digest="sha256:attest-abc")
        ),
    )

    connector = registry.register(
        ConnectorRegistrationRequest(
            name="koko-1",
            environment="prod",
            public_key_fingerprint="sha256:abc",
            mtls_certificate_fingerprint="sha256:cert-abc",
            capabilities=[ConnectorCapability.SSH],
            enrollment_token="token-1",
            attestation_nonce="nonce-1",
            attestation_digest="sha256:attest-abc",
        )
    )

    assert connector.attestation_nonce == "nonce-1"
    assert connector.attestation_digest == "sha256:attest-abc"


def test_mtls_certificate_mismatch_blocks_connection_token():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(
            _active_token(mtls_certificate_fingerprint="sha256:cert-abc")
        ),
    )
    connector = registry.register(_registration_request())

    with pytest.raises(ValueError, match="CONNECTOR_MTLS_CERTIFICATE_MISMATCH"):
        registry.issue_connection_token(
            connector.id,
            request={"action": "asset.connect"},
            policy_service=StubPolicyService(PolicyDecision.ALLOW),
            mtls_certificate_fingerprint="sha256:cert-other",
        )


def test_connector_heartbeat_refreshes_lease_timestamp():
    store = InMemoryConnectorStore()
    registry = ConnectorRegistry(store=store, enrollment_tokens=_enrollment_tokens(_active_token()))
    connector = registry.register(_registration_request())
    heartbeat_at = datetime.now(UTC) + timedelta(seconds=30)

    updated = registry.record_heartbeat(connector.id, heartbeat_at=heartbeat_at)

    assert updated.last_heartbeat_at == heartbeat_at
    assert updated.status == ConnectorStatus.ACTIVE
    assert store.get(connector.id).last_heartbeat_at == heartbeat_at


def test_stale_connector_heartbeat_blocks_connection_token():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(_active_token()),
        heartbeat_timeout_seconds=30,
    )
    connector = registry.register(_registration_request())
    registry.record_heartbeat(
        connector.id,
        heartbeat_at=datetime.now(UTC) - timedelta(seconds=31),
    )

    with pytest.raises(ValueError, match="CONNECTOR_HEARTBEAT_EXPIRED"):
        registry.issue_connection_token(
            connector.id,
            request={"action": "asset.connect"},
            policy_service=StubPolicyService(PolicyDecision.ALLOW),
        )


def test_inactive_connector_heartbeat_is_rejected():
    store = InMemoryConnectorStore()
    registry = ConnectorRegistry(store=store, enrollment_tokens=_enrollment_tokens(_active_token()))
    connector = registry.register(_registration_request())
    store.set_status(connector.id, ConnectorStatus.INACTIVE)

    with pytest.raises(ValueError, match="CONNECTOR_NOT_ACTIVE"):
        registry.record_heartbeat(connector.id)

    assert store.get(connector.id).status == ConnectorStatus.INACTIVE


def test_active_connector_key_rotation_updates_fingerprint_and_history():
    store = InMemoryConnectorStore()
    registry = ConnectorRegistry(store=store, enrollment_tokens=_enrollment_tokens(_active_token()))
    connector = registry.register(_registration_request())
    rotated_at = datetime.now(UTC) + timedelta(minutes=1)

    updated = registry.rotate_key(
        connector.id,
        public_key_fingerprint="sha256:def",
        rotated_at=rotated_at,
    )

    assert updated.public_key_fingerprint == "sha256:def"
    assert updated.previous_public_key_fingerprint == "sha256:abc"
    assert updated.key_rotated_at == rotated_at
    assert store.get(connector.id).public_key_fingerprint == "sha256:def"


def test_inactive_connector_key_rotation_is_rejected():
    store = InMemoryConnectorStore()
    registry = ConnectorRegistry(store=store, enrollment_tokens=_enrollment_tokens(_active_token()))
    connector = registry.register(_registration_request())
    store.set_status(connector.id, ConnectorStatus.INACTIVE)

    with pytest.raises(ValueError, match="CONNECTOR_NOT_ACTIVE"):
        registry.rotate_key(connector.id, public_key_fingerprint="sha256:def")

    assert store.get(connector.id).public_key_fingerprint == "sha256:abc"


def test_enrollment_token_cannot_be_reused_after_successful_registration():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(_active_token()),
    )
    request = _registration_request()

    registry.register(request)

    with pytest.raises(ValueError, match="ENROLLMENT_TOKEN_ALREADY_USED"):
        registry.register(request)


def test_expired_enrollment_token_is_rejected():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(
            ConnectorEnrollmentToken.from_plaintext(
                "token-1",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        ),
    )

    with pytest.raises(ValueError, match="ENROLLMENT_TOKEN_EXPIRED"):
        registry.register(_registration_request())


def test_enrollment_token_bound_to_fingerprint_rejects_mismatch():
    registry = ConnectorRegistry(
        store=InMemoryConnectorStore(),
        enrollment_tokens=_enrollment_tokens(
            _active_token(public_key_fingerprint="sha256:expected")
        ),
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


def test_enrollment_token_does_not_store_plaintext_value():
    token = _active_token("super-secret-token")

    assert not hasattr(token, "value")
    assert token.token_digest == ConnectorEnrollmentToken.digest("super-secret-token")
    assert token.token_digest != "super-secret-token"
