"""Connector registry and short-lived token issuance."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from app.connectors.schemas import (
    ConnectionToken,
    ConnectorRecord,
    ConnectorRegistrationRequest,
    ConnectorStatus,
)
from app.policy.schemas import PolicyDecision, PolicyDecisionResponse


class PolicyEvaluator(Protocol):
    def evaluate(self, request: dict[str, Any]) -> PolicyDecisionResponse: ...


@dataclass
class ConnectorEnrollmentToken:
    """Single-use connector enrollment token with optional binding.

    Stores only token digest, never the plaintext enrollment token.
    """

    token_digest: str
    expires_at: datetime
    public_key_fingerprint: str | None = None
    mtls_certificate_fingerprint: str | None = None
    connector_name: str | None = None
    environment: str | None = None
    used_at: datetime | None = None

    @classmethod
    def from_plaintext(
        cls,
        plaintext_token: str,
        expires_at: datetime,
        public_key_fingerprint: str | None = None,
        mtls_certificate_fingerprint: str | None = None,
        connector_name: str | None = None,
        environment: str | None = None,
    ) -> ConnectorEnrollmentToken:
        return cls(
            token_digest=cls.digest(plaintext_token),
            expires_at=expires_at,
            public_key_fingerprint=public_key_fingerprint,
            mtls_certificate_fingerprint=mtls_certificate_fingerprint,
            connector_name=connector_name,
            environment=environment,
        )

    @staticmethod
    def digest(plaintext_token: str) -> str:
        return hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()

    def validate_for(self, request: ConnectorRegistrationRequest) -> None:
        now = datetime.now(UTC)
        if self.used_at is not None:
            raise ValueError("ENROLLMENT_TOKEN_ALREADY_USED")
        if self.expires_at <= now:
            raise ValueError("ENROLLMENT_TOKEN_EXPIRED")
        if (
            self.public_key_fingerprint is not None
            and self.public_key_fingerprint != request.public_key_fingerprint
        ):
            raise ValueError("ENROLLMENT_TOKEN_BINDING_MISMATCH")
        if (
            self.mtls_certificate_fingerprint is not None
            and self.mtls_certificate_fingerprint != request.mtls_certificate_fingerprint
        ):
            raise ValueError("ENROLLMENT_TOKEN_BINDING_MISMATCH")
        if self.connector_name is not None and self.connector_name != request.name:
            raise ValueError("ENROLLMENT_TOKEN_BINDING_MISMATCH")
        if self.environment is not None and self.environment != request.environment:
            raise ValueError("ENROLLMENT_TOKEN_BINDING_MISMATCH")

    def mark_used(self) -> None:
        self.used_at = datetime.now(UTC)


class InMemoryConnectorStore:
    """Small in-memory store for initial service tests and development."""

    def __init__(self) -> None:
        self._records: dict[str, ConnectorRecord] = {}

    def save(self, record: ConnectorRecord) -> ConnectorRecord:
        self._records[record.id] = record
        return record

    def get(self, connector_id: str) -> ConnectorRecord | None:
        return self._records.get(connector_id)

    def set_status(self, connector_id: str, status: ConnectorStatus) -> None:
        record = self._records[connector_id]
        self._records[connector_id] = record.model_copy(update={"status": status})

    def record_heartbeat(self, connector_id: str, heartbeat_at: datetime) -> ConnectorRecord:
        record = self._records[connector_id]
        updated = record.model_copy(
            update={
                "status": ConnectorStatus.ACTIVE,
                "last_heartbeat_at": heartbeat_at,
            }
        )
        self._records[connector_id] = updated
        return updated


class ConnectorRegistry:
    """Registers trusted connectors and gates connection-token issuance."""

    def __init__(
        self,
        store: InMemoryConnectorStore,
        enrollment_tokens: dict[str, ConnectorEnrollmentToken],
        heartbeat_timeout_seconds: int = 120,
    ) -> None:
        self._store = store
        self._enrollment_tokens = enrollment_tokens
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds

    def register(self, request: ConnectorRegistrationRequest) -> ConnectorRecord:
        token_digest = ConnectorEnrollmentToken.digest(request.enrollment_token)
        enrollment_token = self._enrollment_tokens.get(token_digest)
        if enrollment_token is None:
            raise ValueError("INVALID_ENROLLMENT_TOKEN")
        enrollment_token.validate_for(request)
        if not request.public_key_fingerprint.startswith("sha256:"):
            raise ValueError("INVALID_CONNECTOR_FINGERPRINT")
        if (
            enrollment_token.mtls_certificate_fingerprint is not None
            and not enrollment_token.mtls_certificate_fingerprint.startswith("sha256:")
        ):
            raise ValueError("INVALID_CONNECTOR_MTLS_FINGERPRINT")

        now = datetime.now(UTC)
        record = ConnectorRecord(
            id=f"conn_{uuid4().hex}",
            name=request.name,
            environment=request.environment,
            public_key_fingerprint=request.public_key_fingerprint,
            mtls_certificate_fingerprint=enrollment_token.mtls_certificate_fingerprint,
            capabilities=request.capabilities,
            registered_at=now,
            last_heartbeat_at=now,
        )
        enrollment_token.mark_used()
        return self._store.save(record)

    def record_heartbeat(
        self, connector_id: str, heartbeat_at: datetime | None = None
    ) -> ConnectorRecord:
        connector = self._store.get(connector_id)
        if connector is None:
            raise ValueError("CONNECTOR_NOT_FOUND")
        if connector.status != ConnectorStatus.ACTIVE:
            raise ValueError("CONNECTOR_NOT_ACTIVE")
        return self._store.record_heartbeat(connector_id, heartbeat_at or datetime.now(UTC))

    def issue_connection_token(
        self,
        connector_id: str,
        request: dict[str, Any],
        policy_service: PolicyEvaluator,
        mtls_certificate_fingerprint: str | None = None,
    ) -> ConnectionToken:
        connector = self._store.get(connector_id)
        if connector is None:
            raise ValueError("CONNECTOR_NOT_FOUND")
        if connector.status != ConnectorStatus.ACTIVE:
            raise ValueError("CONNECTOR_NOT_ACTIVE")
        if (
            connector.mtls_certificate_fingerprint is not None
            and connector.mtls_certificate_fingerprint != mtls_certificate_fingerprint
        ):
            raise ValueError("CONNECTOR_MTLS_CERTIFICATE_MISMATCH")
        if self._heartbeat_expired(connector):
            raise ValueError("CONNECTOR_HEARTBEAT_EXPIRED")

        policy_result = policy_service.evaluate(request)
        if policy_result.decision != PolicyDecision.ALLOW:
            raise ValueError("POLICY_DENIED")

        ttl = min(
            policy_result.ttl_seconds or 60,
            policy_result.obligations.get("max_session_ttl_seconds", 60),
        )
        return ConnectionToken(
            connector_id=connector.id,
            token=f"jgt_{secrets.token_urlsafe(32)}",
            ttl_seconds=ttl,
            policy_audit_event_id=policy_result.audit_event_id,
        )

    def _heartbeat_expired(self, connector: ConnectorRecord) -> bool:
        if connector.last_heartbeat_at is None:
            return True
        elapsed = datetime.now(UTC) - connector.last_heartbeat_at
        return elapsed.total_seconds() > self._heartbeat_timeout_seconds
