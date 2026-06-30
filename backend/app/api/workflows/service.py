"""Workflow/JIT request state machine and in-memory repository."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.api.sessions.service import JitGrantSessionBinding

MAX_GRANT_TTL_SECONDS = 86_400


class WorkflowRequestStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


class JitGrantStatus(StrEnum):
    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"


WORKFLOW_TRANSITIONS: dict[WorkflowRequestStatus, set[WorkflowRequestStatus]] = {
    WorkflowRequestStatus.DRAFT: {WorkflowRequestStatus.PENDING},
    WorkflowRequestStatus.PENDING: {
        WorkflowRequestStatus.APPROVED,
        WorkflowRequestStatus.REJECTED,
        WorkflowRequestStatus.EXPIRED,
        WorkflowRequestStatus.REVOKED,
    },
    WorkflowRequestStatus.APPROVED: {
        WorkflowRequestStatus.EXPIRED,
        WorkflowRequestStatus.REVOKED,
    },
    WorkflowRequestStatus.REJECTED: set(),
    WorkflowRequestStatus.EXPIRED: set(),
    WorkflowRequestStatus.REVOKED: set(),
}


class WorkflowRequestRecord(BaseModel):
    id: str
    tenant_id: str
    requester_id: str
    requester_username: str = ""
    asset_id: str
    account_id: str
    protocol: str
    action: str
    reason: str
    requested_ttl_seconds: int
    status: WorkflowRequestStatus = WorkflowRequestStatus.DRAFT
    created_at: datetime
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    decision_reason: str = ""
    approver_id: str = ""
    approver_username: str = ""
    grant_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class JitGrantRecord(BaseModel):
    id: str
    tenant_id: str
    workflow_request_id: str
    subject_id: str
    asset_id: str
    account_id: str
    protocol: str
    action: str
    status: JitGrantStatus = JitGrantStatus.ACTIVE
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    max_session_ttl_seconds: int
    constraints: dict[str, Any] = Field(
        default_factory=lambda: {"usage": "single-use", "max_uses": 1, "used_count": 0}
    )


class AuditSink(Protocol):
    async def publish(self, event: dict[str, Any]) -> None:
        """Publish workflow/JIT lifecycle events."""


class SessionRevoker(Protocol):
    async def revoke_sessions_by_jit_grant(self, jit_grant_id: str, reason: str) -> list[str]:
        """Close active sessions bound to a JIT grant."""


class NoopAuditSink:
    async def publish(self, event: dict[str, Any]) -> None:
        return None


class NoopSessionRevoker:
    async def revoke_sessions_by_jit_grant(self, jit_grant_id: str, reason: str) -> list[str]:
        return []


class InMemoryWorkflowStore:
    def __init__(self) -> None:
        self._requests: dict[str, WorkflowRequestRecord] = {}
        self._grants: dict[str, JitGrantRecord] = {}

    async def save_request(self, request: WorkflowRequestRecord) -> WorkflowRequestRecord:
        self._requests[request.id] = request
        return request

    async def get_request(self, request_id: str) -> WorkflowRequestRecord | None:
        return self._requests.get(request_id)

    async def list_requests(self, *, tenant_id: str, requester_id: str | None) -> list[WorkflowRequestRecord]:
        return [
            request
            for request in self._requests.values()
            if request.tenant_id == tenant_id
            and (requester_id is None or request.requester_id == requester_id)
        ]

    async def save_grant(self, grant: JitGrantRecord) -> JitGrantRecord:
        self._grants[grant.id] = grant
        return grant

    async def get_grant(self, grant_id: str) -> JitGrantRecord | None:
        return self._grants.get(grant_id)

    async def list_active_grants(self, *, tenant_id: str, now: datetime) -> list[JitGrantRecord]:
        return [
            grant
            for grant in self._grants.values()
            if grant.tenant_id == tenant_id
            and grant.status is JitGrantStatus.ACTIVE
            and grant.expires_at > now
        ]

    def clear(self) -> None:
        self._requests.clear()
        self._grants.clear()


class WorkflowService:
    def __init__(
        self,
        *,
        store: InMemoryWorkflowStore | None = None,
        audit_sink: AuditSink | None = None,
        session_revoker: SessionRevoker | None = None,
        now: Callable[[], datetime] | None = None,
        request_id_factory: Callable[[], str] | None = None,
        grant_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store or InMemoryWorkflowStore()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.session_revoker = session_revoker or NoopSessionRevoker()
        self.now = now or (lambda: datetime.now(UTC))
        self.request_id_factory = request_id_factory or (lambda: f"wr_{uuid.uuid4().hex}")
        self.grant_id_factory = grant_id_factory or (lambda: f"jg_{uuid.uuid4().hex}")

    async def create_request(
        self,
        *,
        actor: dict[str, Any],
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        reason: str,
        requested_ttl_seconds: int,
        metadata: dict[str, Any],
    ) -> WorkflowRequestRecord:
        self._validate_ttl(requested_ttl_seconds)
        request = WorkflowRequestRecord(
            id=self.request_id_factory(),
            tenant_id=str(actor.get("tenant_id", "default")),
            requester_id=str(actor["id"]),
            requester_username=str(actor.get("username", "")),
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            reason=reason,
            requested_ttl_seconds=requested_ttl_seconds,
            created_at=self.now(),
            metadata=metadata,
        )
        await self.store.save_request(request)
        await self._publish("workflow.request.created", request)
        return request

    async def submit_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        tenant_id: str,
    ) -> WorkflowRequestRecord:
        request = await self._get_request_for_tenant(request_id, tenant_id)
        if request.requester_id != actor_id:
            raise PermissionError("WORKFLOW_REQUESTER_MISMATCH")
        self._transition(request, WorkflowRequestStatus.PENDING)
        request.submitted_at = self.now()
        await self.store.save_request(request)
        await self._publish("workflow.request.submitted", request)
        return request

    async def approve_request(
        self,
        request_id: str,
        *,
        actor: dict[str, Any],
        decision_reason: str,
        grant_ttl_seconds: int,
    ) -> WorkflowRequestRecord:
        self._require_approve_permission(actor)
        self._validate_ttl(grant_ttl_seconds)
        request = await self._get_request_for_tenant(request_id, str(actor.get("tenant_id", "default")))
        if request.requester_id == str(actor["id"]):
            raise PermissionError("SELF_APPROVAL_NOT_ALLOWED")
        self._transition(request, WorkflowRequestStatus.APPROVED)
        now = self.now()
        grant = JitGrantRecord(
            id=self.grant_id_factory(),
            tenant_id=request.tenant_id,
            workflow_request_id=request.id,
            subject_id=request.requester_id,
            asset_id=request.asset_id,
            account_id=request.account_id,
            protocol=request.protocol,
            action=request.action,
            issued_at=now,
            expires_at=now + timedelta(seconds=min(grant_ttl_seconds, request.requested_ttl_seconds)),
            max_session_ttl_seconds=min(grant_ttl_seconds, request.requested_ttl_seconds),
        )
        request.decided_at = now
        request.expires_at = grant.expires_at
        request.decision_reason = decision_reason
        request.approver_id = str(actor["id"])
        request.approver_username = str(actor.get("username", ""))
        request.grant_id = grant.id
        await self.store.save_grant(grant)
        await self.store.save_request(request)
        await self._publish("workflow.request.approved", request, decision_reason=decision_reason)
        await self._publish_grant("jit.grant.issued", grant, request)
        return request

    async def reject_request(
        self,
        request_id: str,
        *,
        actor: dict[str, Any],
        decision_reason: str,
    ) -> WorkflowRequestRecord:
        self._require_approve_permission(actor)
        if not decision_reason:
            raise ValueError("DECISION_REASON_REQUIRED")
        request = await self._get_request_for_tenant(request_id, str(actor.get("tenant_id", "default")))
        if request.requester_id == str(actor["id"]):
            raise PermissionError("SELF_APPROVAL_NOT_ALLOWED")
        self._transition(request, WorkflowRequestStatus.REJECTED)
        request.decided_at = self.now()
        request.decision_reason = decision_reason
        request.approver_id = str(actor["id"])
        request.approver_username = str(actor.get("username", ""))
        await self.store.save_request(request)
        await self._publish("workflow.request.rejected", request, decision_reason=decision_reason)
        return request

    async def revoke_request(
        self,
        request_id: str,
        *,
        actor: dict[str, Any],
        reason: str,
    ) -> WorkflowRequestRecord:
        request = await self._get_request_for_tenant(request_id, str(actor.get("tenant_id", "default")))
        actor_id = str(actor["id"])
        requester_can_revoke = (
            request.requester_id == actor_id and request.status is WorkflowRequestStatus.PENDING
        )
        privileged_can_revoke = self._has_any_permission(
            actor,
            {"workflow:approve", "workflow:admin", "admin"},
        )
        if not requester_can_revoke and not privileged_can_revoke:
            raise PermissionError("WORKFLOW_REVOKE_NOT_ALLOWED")
        self._transition(request, WorkflowRequestStatus.REVOKED)
        request.revoked_at = self.now()
        request.decision_reason = reason
        if request.grant_id:
            grant = await self.get_grant(request.grant_id, tenant_id=request.tenant_id)
            if grant is not None and grant.status in {JitGrantStatus.ACTIVE, JitGrantStatus.USED}:
                grant.status = JitGrantStatus.REVOKED
                grant.revoked_at = self.now()
                await self.store.save_grant(grant)
                await self.session_revoker.revoke_sessions_by_jit_grant(
                    grant.id,
                    reason="jit_grant_revoked",
                )
                await self._publish_grant("jit.grant.revoked", grant, request)
        await self.store.save_request(request)
        await self._publish("workflow.request.revoked", request, decision_reason=reason)
        return request

    async def list_requests(
        self,
        *,
        actor: dict[str, Any],
    ) -> list[WorkflowRequestRecord]:
        requester_id = None if "workflow:approve" in actor.get("permissions", []) else str(actor["id"])
        return await self.store.list_requests(
            tenant_id=str(actor.get("tenant_id", "default")),
            requester_id=requester_id,
        )

    async def get_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestRecord | None:
        request = await self.store.get_request(request_id)
        if request is None or request.tenant_id != tenant_id:
            return None
        return request

    async def get_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantRecord | None:
        grant = await self.store.get_grant(grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            return None
        return grant

    async def list_active_grants(self, *, actor: dict[str, Any]) -> list[JitGrantRecord]:
        grants = await self.store.list_active_grants(
            tenant_id=str(actor.get("tenant_id", "default")),
            now=self.now(),
        )
        if self._can_view_tenant_grants(actor):
            return grants
        actor_id = str(actor["id"])
        return [grant for grant in grants if grant.subject_id == actor_id]

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
        grant = await self.get_grant(jit_grant_id, tenant_id=tenant_id)
        if grant is None:
            raise PermissionError("JIT_GRANT_NOT_FOUND")
        if grant.status is not JitGrantStatus.ACTIVE:
            raise PermissionError(f"JIT_GRANT_NOT_ACTIVE:{grant.status}")
        if grant.expires_at <= now:
            grant.status = JitGrantStatus.EXPIRED
            await self.store.save_grant(grant)
            raise PermissionError("JIT_GRANT_EXPIRED")
        if grant.subject_id != subject_id:
            raise PermissionError("JIT_GRANT_SUBJECT_MISMATCH")
        if grant.asset_id != asset_id:
            raise PermissionError("JIT_GRANT_ASSET_MISMATCH")
        if grant.account_id != account_id:
            raise PermissionError("JIT_GRANT_ACCOUNT_MISMATCH")
        if grant.protocol != protocol:
            raise PermissionError("JIT_GRANT_PROTOCOL_MISMATCH")
        if grant.action != action:
            raise PermissionError("JIT_GRANT_ACTION_MISMATCH")
        used_count = int(grant.constraints.get("used_count", 0))
        max_uses = int(grant.constraints.get("max_uses", 1))
        if used_count >= max_uses:
            raise PermissionError("JIT_GRANT_USAGE_EXHAUSTED")
        return JitGrantSessionBinding(
            jit_grant_id=grant.id,
            workflow_request_id=grant.workflow_request_id,
            expires_at=grant.expires_at,
            constraints=grant.constraints,
        )

    async def mark_session_bound(self, *, jit_grant_id: str, session_id: str) -> None:
        grant = await self.store.get_grant(jit_grant_id)
        if grant is None:
            raise PermissionError("JIT_GRANT_NOT_FOUND")
        used_count = int(grant.constraints.get("used_count", 0)) + 1
        grant.constraints["used_count"] = used_count
        if used_count >= int(grant.constraints.get("max_uses", 1)):
            grant.status = JitGrantStatus.USED
        await self.store.save_grant(grant)
        request = await self.store.get_request(grant.workflow_request_id)
        if request is not None:
            await self._publish_grant("jit.grant.used", grant, request, session_id=session_id)

    async def _get_request_for_tenant(self, request_id: str, tenant_id: str) -> WorkflowRequestRecord:
        request = await self.get_request(request_id, tenant_id=tenant_id)
        if request is None:
            raise ValueError("WORKFLOW_REQUEST_NOT_FOUND")
        return request

    def _transition(
        self,
        request: WorkflowRequestRecord,
        next_status: WorkflowRequestStatus,
    ) -> None:
        if next_status not in WORKFLOW_TRANSITIONS[request.status]:
            raise ValueError(f"INVALID_WORKFLOW_TRANSITION:{request.status}->{next_status}")
        request.status = next_status

    def _require_approve_permission(self, actor: dict[str, Any]) -> None:
        if not self._has_any_permission(actor, {"workflow:approve", "workflow:admin", "admin"}):
            raise PermissionError("WORKFLOW_APPROVE_NOT_ALLOWED")

    def _validate_ttl(self, ttl_seconds: int) -> None:
        if ttl_seconds <= 0 or ttl_seconds > MAX_GRANT_TTL_SECONDS:
            raise ValueError("INVALID_GRANT_TTL")

    def _can_view_tenant_grants(self, actor: dict[str, Any]) -> bool:
        return self._has_any_permission(
            actor,
            {"workflow:approve", "workflow:audit", "workflow:admin", "audit:read", "admin"},
        )

    def _has_any_permission(self, actor: dict[str, Any], permissions: set[str]) -> bool:
        return bool(permissions.intersection(set(actor.get("permissions", []))))

    async def _publish(
        self,
        event_type: str,
        request: WorkflowRequestRecord,
        *,
        decision_reason: str = "",
    ) -> None:
        await self.audit_sink.publish(
            {
                "id": uuid.uuid4().hex,
                "type": event_type,
                "workflow_request_id": request.id,
                "jit_grant_id": request.grant_id,
                "tenant_id": request.tenant_id,
                "requester_id": request.requester_id,
                "approver_id": request.approver_id,
                "asset_id": request.asset_id,
                "account_id": request.account_id,
                "protocol": request.protocol,
                "action": request.action,
                "status": request.status.value,
                "decision_reason": decision_reason,
                "occurred_at": self.now().isoformat(),
            }
        )

    async def _publish_grant(
        self,
        event_type: str,
        grant: JitGrantRecord,
        request: WorkflowRequestRecord,
        *,
        session_id: str = "",
    ) -> None:
        await self.audit_sink.publish(
            {
                "id": uuid.uuid4().hex,
                "type": event_type,
                "workflow_request_id": request.id,
                "jit_grant_id": grant.id,
                "session_id": session_id,
                "tenant_id": grant.tenant_id,
                "subject_id": grant.subject_id,
                "asset_id": grant.asset_id,
                "account_id": grant.account_id,
                "protocol": grant.protocol,
                "action": grant.action,
                "status": grant.status.value,
                "occurred_at": self.now().isoformat(),
            }
        )
