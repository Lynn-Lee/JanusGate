"""Session Gateway service and lifecycle state machine.

Phase 1 keeps the service in the sessions bounded context to avoid touching the
shared ORM/model owner area before persistence contracts are finalized.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.policy.decision import PolicyDecisionService
from app.policy.schemas import ApprovalState, PolicyDecisionRequest, ResourceRef, SubjectRef


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
    tenant_id: str = ""
    asset_id: str
    account_id: str
    protocol: str = ""
    action: str = "session.connect"
    jit_grant_id: str = ""
    workflow_request_id: str = ""
    connector_id: str
    expires_at: datetime


class ConnectionTokenIssue(BaseModel):
    connection_token: str
    expires_at: datetime
    subject_id: str
    tenant_id: str
    asset_id: str
    account_id: str
    protocol: str
    action: str
    jit_grant_id: str
    workflow_request_id: str


class SessionRecord(BaseModel):
    id: str
    subject_id: str
    tenant_id: str = "default"
    asset_id: str
    account_id: str
    connector_id: str = ""
    protocol: str
    status: SessionStatus = SessionStatus.REQUESTED
    connection_token_id: str
    connector_session_id: str = ""
    connection_url: str = ""
    client_ip: str = ""
    client_ip_source: str = ""
    workflow_request_id: str = ""
    jit_grant_id: str = ""
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    failure_reason: str = ""
    audit_event_ids: list[str] = Field(default_factory=list)


class PolicyDecisionClient(Protocol):
    async def evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return a PolicyDecisionService-compatible decision payload."""


class ConnectionTokenStore(Protocol):
    async def issue(self, token: ConnectionToken) -> ConnectionTokenIssue:
        """Store token metadata and return the raw short-lived token once."""

    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        """Consume and return a short-lived connection token."""


class ConnectorScheduler(Protocol):
    async def dispatch(self, session_id: str, connector_id: str) -> dict[str, str]:
        """Dispatch the session to the selected connector."""


class AuditSink(Protocol):
    async def publish(self, event: dict[str, Any]) -> None:
        """Publish session lifecycle events for the audit module."""


class JitGrantSessionBinding(BaseModel):
    jit_grant_id: str
    workflow_request_id: str
    expires_at: datetime
    constraints: dict[str, Any] = Field(default_factory=dict)


class JitGrantClient(Protocol):
    async def get_grant(self, grant_id: str, *, tenant_id: str) -> Any | None:
        """Return a grant snapshot without consuming it."""

    async def validate_for_session(
        self,
        *,
        jit_grant_id: str,
        subject_id: str,
        tenant_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantSessionBinding:
        """Validate a grant for one session creation attempt."""

    async def mark_session_bound(self, *, jit_grant_id: str, session_id: str) -> None:
        """Mark a grant as bound/used after a session is active."""


class DenyAllPolicyClient:
    async def evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision": "deny",
            "reason_code": "POLICY_CLIENT_NOT_CONFIGURED",
            "explain": ["Session Gateway is fail-closed until PolicyDecisionService is bound."],
            "ttl_seconds": 0,
            "obligations": [],
        }


class PolicyDecisionServiceClient:
    """Adapter from Session Gateway's dict contract into PolicyDecisionService."""

    def __init__(self, service: PolicyDecisionService) -> None:
        self.service = service

    async def evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        subject = request.get("subject", {})
        resource = request.get("resource", {})
        approval = request.get("approval")
        response = self.service.evaluate(
            PolicyDecisionRequest(
                subject=SubjectRef(
                    id=str(subject.get("id", "")),
                    type=str(subject.get("type", "user")),
                    tenant_id=str(subject.get("tenant_id", "default")),
                ),
                action=str(request.get("action", "")),
                resource=ResourceRef(
                    id=str(resource.get("id") or resource.get("asset_id", "")),
                    type=str(resource.get("type", "asset")),
                    tenant_id=str(resource.get("tenant_id", "default")),
                ),
                context=dict(request.get("context", {})),
                approval=ApprovalState(**approval) if isinstance(approval, dict) else None,
                connector_trusted=bool(request.get("connector_trusted", False)),
            )
        )
        return response.model_dump(mode="json")


class EmptyConnectionTokenStore:
    async def issue(self, token: ConnectionToken) -> ConnectionTokenIssue:
        raise ValueError("CONNECTION_TOKEN_STORE_NOT_CONFIGURED")

    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        raise ValueError("CONNECTION_TOKEN_NOT_FOUND")


class InMemoryConnectionTokenStore:
    """Short-lived opaque connection token store.

    The raw token is returned only to the caller. The store indexes metadata by
    SHA-256 digest and removes entries on consume so frontend-created sessions
    cannot replay the same connection token.
    """

    def __init__(self, *, token_id_factory: Callable[[], str] | None = None) -> None:
        self._token_id_factory = token_id_factory or (lambda: f"jgt_{secrets.token_urlsafe(32)}")
        self._tokens_by_digest: dict[str, ConnectionToken] = {}

    async def issue(self, token: ConnectionToken) -> ConnectionTokenIssue:
        raw_token = self._token_id_factory()
        token_digest = self._digest(raw_token)
        self._tokens_by_digest[token_digest] = token.model_copy(update={"token_id": token_digest})
        return ConnectionTokenIssue(
            connection_token=raw_token,
            expires_at=token.expires_at,
            subject_id=token.subject_id,
            tenant_id=token.tenant_id,
            asset_id=token.asset_id,
            account_id=token.account_id,
            protocol=token.protocol,
            action=token.action,
            jit_grant_id=token.jit_grant_id,
            workflow_request_id=token.workflow_request_id,
        )

    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        token = self._tokens_by_digest.pop(self._digest(token_id), None)
        if token is None:
            raise ValueError("CONNECTION_TOKEN_NOT_FOUND")
        return token

    @staticmethod
    def _digest(token_id: str) -> str:
        return hashlib.sha256(token_id.encode("utf-8")).hexdigest()


class NoopConnectorScheduler:
    async def dispatch(self, session_id: str, connector_id: str) -> dict[str, str]:
        return {
            "connector_session_id": "",
            "connection_url": "",
        }


class NoopAuditSink:
    async def publish(self, event: dict[str, Any]) -> None:
        return None


class NoopJitGrantClient:
    async def get_grant(self, grant_id: str, *, tenant_id: str) -> Any | None:
        return None

    async def validate_for_session(
        self,
        *,
        jit_grant_id: str,
        subject_id: str,
        tenant_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantSessionBinding:
        raise PermissionError("JIT_GRANT_CLIENT_NOT_CONFIGURED")

    async def mark_session_bound(self, *, jit_grant_id: str, session_id: str) -> None:
        raise PermissionError("JIT_GRANT_CLIENT_NOT_CONFIGURED")


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    async def save(self, session: SessionRecord) -> SessionRecord:
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def list_by_jit_grant(self, jit_grant_id: str) -> list[SessionRecord]:
        return [
            session
            for session in self._sessions.values()
            if session.jit_grant_id == jit_grant_id
        ]


class SessionGatewayService:
    def __init__(
        self,
        *,
        policy_client: PolicyDecisionClient | None = None,
        token_store: ConnectionTokenStore | None = None,
        connector_scheduler: ConnectorScheduler | None = None,
        session_store: InMemorySessionStore | None = None,
        audit_sink: AuditSink | None = None,
        jit_grant_client: JitGrantClient | None = None,
        now: Callable[[], datetime] | None = None,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.policy_client = policy_client or DenyAllPolicyClient()
        self.token_store = token_store or EmptyConnectionTokenStore()
        self.connector_scheduler = connector_scheduler or NoopConnectorScheduler()
        self.session_store = session_store or InMemorySessionStore()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.jit_grant_client = jit_grant_client or NoopJitGrantClient()
        self.now = now or (lambda: datetime.now(UTC))
        self.session_id_factory = session_id_factory or (lambda: uuid.uuid4().hex)

    async def issue_connection_token(
        self,
        *,
        subject_id: str,
        tenant_id: str = "default",
        asset_id: str,
        account_id: str,
        protocol: str,
        jit_grant_id: str,
        action: str = "session.connect",
    ) -> ConnectionTokenIssue:
        now = self.now()
        grant = await self.jit_grant_client.get_grant(jit_grant_id, tenant_id=tenant_id)
        if grant is None:
            raise PermissionError("JIT_GRANT_NOT_FOUND")
        self._validate_grant_snapshot_for_token(
            grant,
            subject_id=subject_id,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            now=now,
        )
        grant_expires_at = _comparable_datetime(grant.expires_at, now)
        token_expires_at = min(now + _connection_token_ttl(), grant_expires_at)
        issue = await self.token_store.issue(
            ConnectionToken(
                token_id="",
                subject_id=subject_id,
                tenant_id=tenant_id,
                asset_id=asset_id,
                account_id=account_id,
                protocol=protocol,
                action=action,
                jit_grant_id=jit_grant_id,
                workflow_request_id=str(getattr(grant, "workflow_request_id", "")),
                connector_id=str(getattr(grant, "connector_id", "default-connector")),
                expires_at=token_expires_at,
            )
        )
        await self._publish_connection_token_issued(issue)
        return issue

    async def create_session(
        self,
        *,
        subject_id: str,
        tenant_id: str = "default",
        asset_id: str,
        account_id: str,
        protocol: str,
        connection_token: str,
        client_ip: str = "",
        client_ip_source: str = "direct",
        jit_grant_id: str = "",
    ) -> SessionRecord:
        now = self.now()
        session = SessionRecord(
            id=self.session_id_factory(),
            subject_id=subject_id,
            tenant_id=tenant_id,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            connection_token_id="",
            client_ip=client_ip,
            client_ip_source=client_ip_source,
            jit_grant_id=jit_grant_id,
            created_at=now,
            updated_at=now,
        )
        await self._publish("session.requested", session)

        grant_binding: JitGrantSessionBinding | None = None
        try:
            if jit_grant_id:
                grant_binding = await self.jit_grant_client.validate_for_session(
                    jit_grant_id=jit_grant_id,
                    subject_id=subject_id,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    account_id=account_id,
                    protocol=protocol,
                    action="session.connect",
                    now=now,
                )
                session.workflow_request_id = grant_binding.workflow_request_id
        except Exception as exc:
            reason_code = str(exc)
            self._transition(session, SessionStatus.FAILED)
            session.failure_reason = reason_code
            await self.session_store.save(session)
            await self._publish("session.failed", session, reason_code=reason_code)
            raise

        approval = None
        if grant_binding is not None:
            approval = {
                "status": "approved",
                "grant_id": grant_binding.jit_grant_id,
                "workflow_request_id": grant_binding.workflow_request_id,
                "expires_at": grant_binding.expires_at,
                "constraints": grant_binding.constraints,
            }
        decision = await self.policy_client.evaluate(
            {
                "subject": {"id": subject_id, "type": "user", "tenant_id": tenant_id},
                "action": "session.connect",
                "resource": {
                    "id": asset_id,
                    "type": "asset",
                    "asset_id": asset_id,
                    "account_id": account_id,
                    "protocol": protocol,
                    "tenant_id": tenant_id,
                },
                "context": {
                    "client_ip": client_ip,
                    "client_ip_source": client_ip_source,
                    "account_id": account_id,
                    "protocol": protocol,
                    "jit_grant_id": jit_grant_id,
                    "workflow_request_id": session.workflow_request_id,
                    "jit_grant_constraints": grant_binding.constraints if grant_binding else {},
                },
                "approval": approval,
                "connector_trusted": True,
            }
        )
        if decision.get("decision") != "allow":
            reason_code = str(decision.get("reason_code", "POLICY_DENY"))
            await self._publish("session.denied", session, reason_code=reason_code)
            raise PermissionError(reason_code)

        try:
            token = await self.token_store.consume(connection_token, now)
            self._validate_token(token, session, now)
            session.connection_token_id = token.token_id
            self._transition(session, SessionStatus.AUTHORIZED)
            session.connector_id = token.connector_id
            await self._publish("session.authorized", session)

            if jit_grant_id:
                await self.jit_grant_client.mark_session_bound(
                    jit_grant_id=jit_grant_id,
                    session_id=session.id,
                )

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

    async def revoke_sessions_by_jit_grant(self, jit_grant_id: str, reason: str) -> list[str]:
        sessions = await self.session_store.list_by_jit_grant(jit_grant_id)
        revoked_session_ids: list[str] = []
        for session in sessions:
            if session.status not in {
                SessionStatus.AUTHORIZED,
                SessionStatus.CONNECTING,
                SessionStatus.ACTIVE,
            }:
                continue
            if session.status is SessionStatus.AUTHORIZED:
                self._transition(session, SessionStatus.CLOSED)
            else:
                self._transition(session, SessionStatus.CLOSING)
                self._transition(session, SessionStatus.CLOSED)
            session.closed_at = self.now()
            await self.session_store.save(session)
            await self._publish("session.revoked_by_jit_grant", session, reason_code=reason)
            revoked_session_ids.append(session.id)
        return revoked_session_ids

    def _validate_token(self, token: ConnectionToken, session: SessionRecord, now: datetime) -> None:
        if token.expires_at <= now:
            raise ValueError("CONNECTION_TOKEN_EXPIRED")
        if token.tenant_id and token.tenant_id != session.tenant_id:
            raise ValueError("CONNECTION_TOKEN_TENANT_MISMATCH")
        if token.subject_id != session.subject_id:
            raise ValueError("CONNECTION_TOKEN_SUBJECT_MISMATCH")
        if token.asset_id != session.asset_id:
            raise ValueError("CONNECTION_TOKEN_ASSET_MISMATCH")
        if token.account_id != session.account_id:
            raise ValueError("CONNECTION_TOKEN_ACCOUNT_MISMATCH")
        if token.protocol and token.protocol != session.protocol:
            raise ValueError("CONNECTION_TOKEN_PROTOCOL_MISMATCH")
        if token.jit_grant_id and token.jit_grant_id != session.jit_grant_id:
            raise ValueError("CONNECTION_TOKEN_JIT_GRANT_MISMATCH")
        if token.action and token.action != "session.connect":
            raise ValueError("CONNECTION_TOKEN_ACTION_MISMATCH")
        if not token.connector_id:
            raise ValueError("CONNECTION_TOKEN_CONNECTOR_MISSING")

    def _validate_grant_snapshot_for_token(
        self,
        grant: Any,
        *,
        subject_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> None:
        status = _enum_value(getattr(grant, "status", ""))
        if status != "active":
            raise PermissionError(f"JIT_GRANT_NOT_ACTIVE:{status}")
        expires_at = _comparable_datetime(grant.expires_at, now)
        if expires_at <= now:
            raise PermissionError("JIT_GRANT_EXPIRED")
        if str(getattr(grant, "subject_id", "")) != subject_id:
            raise PermissionError("JIT_GRANT_SUBJECT_MISMATCH")
        if str(getattr(grant, "asset_id", "")) != asset_id:
            raise PermissionError("JIT_GRANT_ASSET_MISMATCH")
        if str(getattr(grant, "account_id", "")) != account_id:
            raise PermissionError("JIT_GRANT_ACCOUNT_MISMATCH")
        if str(getattr(grant, "protocol", "")) != protocol:
            raise PermissionError("JIT_GRANT_PROTOCOL_MISMATCH")
        if str(getattr(grant, "action", "")) != action:
            raise PermissionError("JIT_GRANT_ACTION_MISMATCH")

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
            "tenant_id": session.tenant_id,
            "asset_id": session.asset_id,
            "account_id": session.account_id,
            "connector_id": session.connector_id,
            "workflow_request_id": session.workflow_request_id,
            "jit_grant_id": session.jit_grant_id,
            "status": session.status.value,
            "reason_code": reason_code,
            "client_ip": session.client_ip,
            "client_ip_source": session.client_ip_source,
            "occurred_at": self.now().isoformat(),
        }
        session.audit_event_ids.append(event["id"])
        await self.audit_sink.publish(event)

    async def _publish_connection_token_issued(self, issue: ConnectionTokenIssue) -> None:
        await self.audit_sink.publish(
            {
                "id": uuid.uuid4().hex,
                "type": "session.connection_token.issued",
                "subject_id": issue.subject_id,
                "tenant_id": issue.tenant_id,
                "asset_id": issue.asset_id,
                "account_id": issue.account_id,
                "workflow_request_id": issue.workflow_request_id,
                "jit_grant_id": issue.jit_grant_id,
                "protocol": issue.protocol,
                "action": issue.action,
                "expires_at": issue.expires_at.isoformat(),
                "occurred_at": self.now().isoformat(),
            }
        )


def _connection_token_ttl() -> timedelta:
    return timedelta(minutes=5)


def _comparable_datetime(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
