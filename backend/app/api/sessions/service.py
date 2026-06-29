"""Session Gateway service and lifecycle state machine.

Phase 1 keeps the service in the sessions bounded context to avoid touching the
shared ORM/model owner area before persistence contracts are finalized.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    REQUESTED = "requested"
    AUTHORIZED = "authorized"
    CONNECTING = "connecting"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.REQUESTED: {SessionStatus.AUTHORIZED, SessionStatus.FAILED, SessionStatus.CLOSED},
    SessionStatus.AUTHORIZED: {SessionStatus.CONNECTING, SessionStatus.FAILED, SessionStatus.CLOSED},
    SessionStatus.CONNECTING: {SessionStatus.ACTIVE, SessionStatus.FAILED, SessionStatus.CLOSING},
    SessionStatus.ACTIVE: {SessionStatus.CLOSING, SessionStatus.FAILED},
    SessionStatus.CLOSING: {SessionStatus.CLOSED, SessionStatus.FAILED},
    SessionStatus.CLOSED: set(),
    SessionStatus.FAILED: set(),
}


class ConnectionToken(BaseModel):
    token_id: str
    subject_id: str
    asset_id: str
    account_id: str
    connector_id: str
    expires_at: datetime


class SessionRecord(BaseModel):
    id: str
    subject_id: str
    asset_id: str
    account_id: str
    connector_id: str = ""
    protocol: str
    status: SessionStatus = SessionStatus.REQUESTED
    connection_token_id: str
    connector_session_id: str = ""
    connection_url: str = ""
    client_ip: str = ""
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    failure_reason: str = ""
    audit_event_ids: list[str] = Field(default_factory=list)


class PolicyDecisionClient(Protocol):
    async def evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return a PolicyDecisionService-compatible decision payload."""


class ConnectionTokenStore(Protocol):
    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        """Consume and return a short-lived connection token."""


class ConnectorScheduler(Protocol):
    async def dispatch(self, session_id: str, connector_id: str) -> dict[str, str]:
        """Dispatch the session to the selected connector."""


class AuditSink(Protocol):
    async def publish(self, event: dict[str, Any]) -> None:
        """Publish session lifecycle events for the audit module."""


class DenyAllPolicyClient:
    async def evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision": "deny",
            "reason_code": "POLICY_CLIENT_NOT_CONFIGURED",
            "explain": ["Session Gateway is fail-closed until PolicyDecisionService is bound."],
            "ttl_seconds": 0,
            "obligations": [],
        }


class EmptyConnectionTokenStore:
    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        raise ValueError("CONNECTION_TOKEN_NOT_FOUND")


class NoopConnectorScheduler:
    async def dispatch(self, session_id: str, connector_id: str) -> dict[str, str]:
        return {
            "connector_session_id": "",
            "connection_url": "",
        }


class NoopAuditSink:
    async def publish(self, event: dict[str, Any]) -> None:
        return None


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    async def save(self, session: SessionRecord) -> SessionRecord:
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)


class SessionGatewayService:
    def __init__(
        self,
        *,
        policy_client: PolicyDecisionClient | None = None,
        token_store: ConnectionTokenStore | None = None,
        connector_scheduler: ConnectorScheduler | None = None,
        session_store: InMemorySessionStore | None = None,
        audit_sink: AuditSink | None = None,
        now: Callable[[], datetime] | None = None,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.policy_client = policy_client or DenyAllPolicyClient()
        self.token_store = token_store or EmptyConnectionTokenStore()
        self.connector_scheduler = connector_scheduler or NoopConnectorScheduler()
        self.session_store = session_store or InMemorySessionStore()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.now = now or (lambda: datetime.now(UTC))
        self.session_id_factory = session_id_factory or (lambda: uuid.uuid4().hex)

    async def create_session(
        self,
        *,
        subject_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        connection_token: str,
        client_ip: str = "",
    ) -> SessionRecord:
        now = self.now()
        session = SessionRecord(
            id=self.session_id_factory(),
            subject_id=subject_id,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            connection_token_id=connection_token,
            client_ip=client_ip,
            created_at=now,
            updated_at=now,
        )
        await self._publish("session.requested", session)

        decision = await self.policy_client.evaluate(
            {
                "subject": {"id": subject_id},
                "action": "session.connect",
                "resource": {
                    "asset_id": asset_id,
                    "account_id": account_id,
                    "protocol": protocol,
                },
                "context": {
                    "connection_token": connection_token,
                    "client_ip": client_ip,
                },
            }
        )
        if decision.get("decision") != "allow":
            reason_code = str(decision.get("reason_code", "POLICY_DENY"))
            await self._publish("session.denied", session, reason_code=reason_code)
            raise PermissionError(reason_code)

        try:
            token = await self.token_store.consume(connection_token, now)
            self._validate_token(token, session, now)
            self._transition(session, SessionStatus.AUTHORIZED)
            session.connector_id = token.connector_id
            await self._publish("session.authorized", session)

            self._transition(session, SessionStatus.CONNECTING)
            await self._publish("session.connecting", session)
            dispatch_result = await self.connector_scheduler.dispatch(
                session.id,
                token.connector_id,
            )
            session.connector_session_id = dispatch_result.get("connector_session_id", "")
            session.connection_url = dispatch_result.get("connection_url", "")
            self._transition(session, SessionStatus.ACTIVE)
            await self.session_store.save(session)
            await self._publish("session.active", session)
            return session
        except Exception as exc:
            reason_code = str(exc)
            self._transition(session, SessionStatus.FAILED)
            session.failure_reason = reason_code
            await self.session_store.save(session)
            await self._publish("session.failed", session, reason_code=reason_code)
            raise

    async def close_session(self, *, session_id: str, subject_id: str, reason: str) -> SessionRecord:
        session = await self.session_store.get(session_id)
        if session is None:
            raise ValueError("SESSION_NOT_FOUND")
        if session.subject_id != subject_id:
            raise PermissionError("SESSION_OWNER_MISMATCH")
        if session.status not in {
            SessionStatus.AUTHORIZED,
            SessionStatus.CONNECTING,
            SessionStatus.ACTIVE,
        }:
            raise ValueError("SESSION_NOT_CLOSABLE")

        self._transition(session, SessionStatus.CLOSING)
        await self._publish("session.closing", session, reason_code=reason)
        self._transition(session, SessionStatus.CLOSED)
        session.closed_at = self.now()
        await self.session_store.save(session)
        await self._publish("session.closed", session, reason_code=reason)
        return session

    def _validate_token(self, token: ConnectionToken, session: SessionRecord, now: datetime) -> None:
        if token.expires_at <= now:
            raise ValueError("CONNECTION_TOKEN_EXPIRED")
        if token.subject_id != session.subject_id:
            raise ValueError("CONNECTION_TOKEN_SUBJECT_MISMATCH")
        if token.asset_id != session.asset_id:
            raise ValueError("CONNECTION_TOKEN_ASSET_MISMATCH")
        if token.account_id != session.account_id:
            raise ValueError("CONNECTION_TOKEN_ACCOUNT_MISMATCH")
        if not token.connector_id:
            raise ValueError("CONNECTION_TOKEN_CONNECTOR_MISSING")

    def _transition(self, session: SessionRecord, next_status: SessionStatus) -> None:
        allowed = ALLOWED_TRANSITIONS[session.status]
        if next_status not in allowed:
            raise ValueError(f"INVALID_SESSION_TRANSITION:{session.status}->{next_status}")
        session.status = next_status
        session.updated_at = self.now()

    async def _publish(
        self,
        event_type: str,
        session: SessionRecord,
        *,
        reason_code: str = "",
    ) -> None:
        event = {
            "id": uuid.uuid4().hex,
            "type": event_type,
            "session_id": session.id,
            "subject_id": session.subject_id,
            "asset_id": session.asset_id,
            "account_id": session.account_id,
            "connector_id": session.connector_id,
            "status": session.status.value,
            "reason_code": reason_code,
            "occurred_at": self.now().isoformat(),
        }
        session.audit_event_ids.append(event["id"])
        await self.audit_sink.publish(event)
