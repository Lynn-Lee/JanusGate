"""Connector registry and short-lived token issuance."""
from __future__ import annotations

import secrets
from typing import Any, Protocol
from uuid import uuid4

from app.connectors.schemas import (
    ConnectionToken,
    ConnectorRecord,
    ConnectorRegistrationRequest,
    ConnectorStatus,
)
from app.policy.schemas import PolicyDecision


class PolicyEvaluator(Protocol):
    def evaluate(self, request: dict[str, Any]) -> Any: ...


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


class ConnectorRegistry:
    """Registers trusted connectors and gates connection-token issuance."""

    def __init__(self, store: InMemoryConnectorStore, enrollment_tokens: set[str]) -> None:
        self._store = store
        self._enrollment_tokens = enrollment_tokens

    def register(self, request: ConnectorRegistrationRequest) -> ConnectorRecord:
        if request.enrollment_token not in self._enrollment_tokens:
            raise ValueError("INVALID_ENROLLMENT_TOKEN")
        if not request.public_key_fingerprint.startswith("sha256:"):
            raise ValueError("INVALID_CONNECTOR_FINGERPRINT")

        record = ConnectorRecord(
            id=f"conn_{uuid4().hex}",
            name=request.name,
            environment=request.environment,
            public_key_fingerprint=request.public_key_fingerprint,
            capabilities=request.capabilities,
        )
        return self._store.save(record)

    def issue_connection_token(
        self,
        connector_id: str,
        request: dict[str, Any],
        policy_service: PolicyEvaluator,
    ) -> ConnectionToken:
        connector = self._store.get(connector_id)
        if connector is None:
            raise ValueError("CONNECTOR_NOT_FOUND")
        if connector.status != ConnectorStatus.ACTIVE:
            raise ValueError("CONNECTOR_NOT_ACTIVE")

        policy_result = policy_service.evaluate(request)
        if policy_result.decision != PolicyDecision.ALLOW:
            raise ValueError("POLICY_DENIED")

        ttl = min(policy_result.ttl_seconds or 60, policy_result.obligations.get("max_session_ttl_seconds", 60))
        return ConnectionToken(
            connector_id=connector.id,
            token=f"jgt_{secrets.token_urlsafe(32)}",
            ttl_seconds=ttl,
            policy_audit_event_id=policy_result.audit_event_id,
        )
