"""Workflow/JIT request state machine and in-memory repository."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sessions.service import JitGrantSessionBinding
from app.models.workflow import (
    JitGrantModel,
    WorkflowRequestModel,
)
from app.models.workflow import (
    JitGrantStatus as SQLAlchemyJitGrantStatus,
)
from app.models.workflow import (
    WorkflowRequestStatus as SQLAlchemyWorkflowRequestStatus,
)

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


class WorkflowStore(Protocol):
    async def save_request(self, request: WorkflowRequestRecord) -> WorkflowRequestRecord:
        """Persist a workflow request."""

    async def get_request(self, request_id: str) -> WorkflowRequestRecord | None:
        """Return a workflow request by id."""

    async def list_requests(
        self, *, tenant_id: str, requester_id: str | None
    ) -> list[WorkflowRequestRecord]:
        """List workflow requests in scope."""

    async def save_grant(self, grant: JitGrantRecord) -> JitGrantRecord:
        """Persist a JIT grant."""

    async def get_grant(self, grant_id: str) -> JitGrantRecord | None:
        """Return a JIT grant by id."""

    async def list_active_grants(
        self,
        *,
        tenant_id: str,
        now: datetime,
        subject_id: str | None = None,
    ) -> list[JitGrantRecord]:
        """List active grants in scope."""

    async def reserve_grant_for_session(
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
    ) -> JitGrantRecord:
        """Atomically reserve/consume a JIT grant for one session."""

    async def commit(self) -> None:
        """Commit any pending persistence changes."""


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

    async def list_active_grants(
        self,
        *,
        tenant_id: str,
        now: datetime,
        subject_id: str | None = None,
    ) -> list[JitGrantRecord]:
        return [
            grant
            for grant in self._grants.values()
            if grant.tenant_id == tenant_id
            and grant.status is JitGrantStatus.ACTIVE
            and grant.expires_at > now
            and (subject_id is None or grant.subject_id == subject_id)
        ]

    async def reserve_grant_for_session(
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
    ) -> JitGrantRecord:
        grant = self._grants.get(jit_grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            raise PermissionError("JIT_GRANT_NOT_FOUND")
        _validate_grant_for_session(
            grant,
            subject_id=subject_id,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            now=now,
        )
        _consume_grant(grant)
        self._grants[grant.id] = grant
        return grant

    async def commit(self) -> None:
        return None

    def clear(self) -> None:
        self._requests.clear()
        self._grants.clear()


class SQLAlchemyWorkflowStore:
    """WorkflowService store backed by SQLAlchemy persistence models."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_request(self, request: WorkflowRequestRecord) -> WorkflowRequestRecord:
        model = await self._request_model(request.id)
        if model is None:
            model = WorkflowRequestModel(
                id=request.id,
                tenant_id=request.tenant_id,
                requester_id=request.requester_id,
                requester_username=request.requester_username,
                resource_type="asset",
                asset_id=request.asset_id,
                account_id=request.account_id,
                protocol=request.protocol,
                action=request.action,
                reason=request.reason,
                requested_ttl_seconds=request.requested_ttl_seconds,
                status=SQLAlchemyWorkflowRequestStatus(request.status.value),
                created_at=request.created_at,
                metadata_json=json.dumps(request.metadata, sort_keys=True),
            )
            self._session.add(model)
        self._apply_request_record(model, request)
        await self._session.flush()
        return await self._request_record(model)

    async def get_request(self, request_id: str) -> WorkflowRequestRecord | None:
        model = await self._request_model(request_id)
        if model is None:
            return None
        return await self._request_record(model)

    async def list_requests(
        self, *, tenant_id: str, requester_id: str | None
    ) -> list[WorkflowRequestRecord]:
        stmt = select(WorkflowRequestModel).where(WorkflowRequestModel.tenant_id == tenant_id)
        if requester_id is not None:
            stmt = stmt.where(WorkflowRequestModel.requester_id == requester_id)
        result = await self._session.execute(stmt)
        return [await self._request_record(model) for model in result.scalars().all()]

    async def save_grant(self, grant: JitGrantRecord) -> JitGrantRecord:
        model = await self._grant_model(grant.id)
        if model is None:
            model = JitGrantModel(
                id=grant.id,
                tenant_id=grant.tenant_id,
                workflow_request_id=grant.workflow_request_id,
                subject_id=grant.subject_id,
                asset_id=grant.asset_id,
                account_id=grant.account_id,
                protocol=grant.protocol,
                action=grant.action,
                status=SQLAlchemyJitGrantStatus(grant.status.value),
                issued_at=grant.issued_at,
                expires_at=grant.expires_at,
                max_session_ttl_seconds=grant.max_session_ttl_seconds,
                constraints_json=json.dumps(grant.constraints, sort_keys=True),
            )
            self._session.add(model)
        self._apply_grant_record(model, grant)
        await self._session.flush()
        return self._grant_record(model)

    async def get_grant(self, grant_id: str) -> JitGrantRecord | None:
        model = await self._grant_model(grant_id)
        if model is None:
            return None
        return self._grant_record(model)

    async def list_active_grants(
        self,
        *,
        tenant_id: str,
        now: datetime,
        subject_id: str | None = None,
    ) -> list[JitGrantRecord]:
        stmt = select(JitGrantModel).where(
            JitGrantModel.tenant_id == tenant_id,
            JitGrantModel.status == SQLAlchemyJitGrantStatus.active,
            JitGrantModel.expires_at > now,
        )
        if subject_id is not None:
            stmt = stmt.where(JitGrantModel.subject_id == subject_id)
        result = await self._session.execute(stmt)
        return [self._grant_record(model) for model in result.scalars().all()]

    async def reserve_grant_for_session(
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
    ) -> JitGrantRecord:
        result = await self._session.execute(
            select(JitGrantModel).where(
                JitGrantModel.id == jit_grant_id,
                JitGrantModel.tenant_id == tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise PermissionError("JIT_GRANT_NOT_FOUND")
        grant = self._grant_record(model)
        _validate_grant_for_session(
            grant,
            subject_id=subject_id,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            now=now,
        )
        _consume_grant(grant)
        update_result = await self._session.execute(
            update(JitGrantModel)
            .where(
                JitGrantModel.id == jit_grant_id,
                JitGrantModel.tenant_id == tenant_id,
                JitGrantModel.subject_id == subject_id,
                JitGrantModel.asset_id == asset_id,
                JitGrantModel.account_id == account_id,
                JitGrantModel.protocol == protocol,
                JitGrantModel.action == action,
                JitGrantModel.status == SQLAlchemyJitGrantStatus.active,
                JitGrantModel.expires_at > now,
            )
            .values(
                status=SQLAlchemyJitGrantStatus(grant.status.value),
                constraints_json=json.dumps(grant.constraints, sort_keys=True),
            )
            .execution_options(synchronize_session=False)
        )
        if int(getattr(update_result, "rowcount", 0)) != 1:
            refreshed = await self._grant_model(jit_grant_id)
            if refreshed is None or refreshed.tenant_id != tenant_id:
                raise PermissionError("JIT_GRANT_NOT_FOUND")
            _validate_grant_for_session(
                self._grant_record(refreshed),
                subject_id=subject_id,
                asset_id=asset_id,
                account_id=account_id,
                protocol=protocol,
                action=action,
                now=now,
            )
            raise PermissionError("JIT_GRANT_RESERVATION_CONFLICT")
        await self._session.flush()
        return grant

    async def commit(self) -> None:
        await self._session.commit()

    async def _request_model(self, request_id: str) -> WorkflowRequestModel | None:
        result = await self._session.execute(
            select(WorkflowRequestModel).where(WorkflowRequestModel.id == request_id)
        )
        return result.scalar_one_or_none()

    async def _grant_model(self, grant_id: str) -> JitGrantModel | None:
        result = await self._session.execute(
            select(JitGrantModel).where(JitGrantModel.id == grant_id)
        )
        return result.scalar_one_or_none()

    async def _request_record(self, model: WorkflowRequestModel) -> WorkflowRequestRecord:
        grant_id = ""
        grant_result = await self._session.execute(
            select(JitGrantModel.id).where(JitGrantModel.workflow_request_id == model.id)
        )
        persisted_grant_id = grant_result.scalars().first()
        if persisted_grant_id:
            grant_id = str(persisted_grant_id)
        return WorkflowRequestRecord(
            id=model.id,
            tenant_id=model.tenant_id,
            requester_id=model.requester_id,
            requester_username=model.requester_username,
            asset_id=model.asset_id,
            account_id=model.account_id,
            protocol=model.protocol,
            action=model.action,
            reason=model.reason,
            requested_ttl_seconds=model.requested_ttl_seconds,
            status=WorkflowRequestStatus(_enum_value(model.status)),
            created_at=model.created_at,
            submitted_at=model.submitted_at,
            decided_at=model.decided_at,
            expires_at=model.expires_at,
            revoked_at=model.revoked_at,
            decision_reason=model.decision_reason,
            approver_id=model.approver_id,
            approver_username=model.approver_username,
            grant_id=grant_id,
            metadata=_json_dict(model.metadata_json),
        )

    def _grant_record(self, model: JitGrantModel) -> JitGrantRecord:
        return JitGrantRecord(
            id=model.id,
            tenant_id=model.tenant_id,
            workflow_request_id=model.workflow_request_id,
            subject_id=model.subject_id,
            asset_id=model.asset_id,
            account_id=model.account_id,
            protocol=model.protocol,
            action=model.action,
            status=JitGrantStatus(_enum_value(model.status)),
            issued_at=model.issued_at,
            expires_at=model.expires_at,
            revoked_at=model.revoked_at,
            max_session_ttl_seconds=model.max_session_ttl_seconds,
            constraints=_json_dict(model.constraints_json),
        )

    def _apply_request_record(
        self,
        model: WorkflowRequestModel,
        request: WorkflowRequestRecord,
    ) -> None:
        model.tenant_id = request.tenant_id
        model.requester_id = request.requester_id
        model.requester_username = request.requester_username
        model.asset_id = request.asset_id
        model.account_id = request.account_id
        model.protocol = request.protocol
        model.action = request.action
        model.reason = request.reason
        model.requested_ttl_seconds = request.requested_ttl_seconds
        model.status = SQLAlchemyWorkflowRequestStatus(request.status.value)
        model.created_at = request.created_at
        model.submitted_at = request.submitted_at
        model.decided_at = request.decided_at
        model.expires_at = request.expires_at
        model.revoked_at = request.revoked_at
        model.decision_reason = request.decision_reason
        model.approver_id = request.approver_id
        model.approver_username = request.approver_username
        model.metadata_json = json.dumps(request.metadata, sort_keys=True)

    def _apply_grant_record(self, model: JitGrantModel, grant: JitGrantRecord) -> None:
        model.tenant_id = grant.tenant_id
        model.workflow_request_id = grant.workflow_request_id
        model.subject_id = grant.subject_id
        model.asset_id = grant.asset_id
        model.account_id = grant.account_id
        model.protocol = grant.protocol
        model.action = grant.action
        model.status = SQLAlchemyJitGrantStatus(grant.status.value)
        model.issued_at = grant.issued_at
        model.expires_at = grant.expires_at
        model.revoked_at = grant.revoked_at
        model.max_session_ttl_seconds = grant.max_session_ttl_seconds
        model.constraints_json = json.dumps(grant.constraints, sort_keys=True)


def _json_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _validate_grant_for_session(
    grant: JitGrantRecord,
    *,
    subject_id: str,
    asset_id: str,
    account_id: str,
    protocol: str,
    action: str,
    now: datetime,
) -> None:
    if grant.status is not JitGrantStatus.ACTIVE:
        raise PermissionError(f"JIT_GRANT_NOT_ACTIVE:{grant.status}")
    if _comparable_datetime(grant.expires_at, now) <= now:
        grant.status = JitGrantStatus.EXPIRED
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


def _comparable_datetime(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _consume_grant(grant: JitGrantRecord) -> None:
    used_count = int(grant.constraints.get("used_count", 0)) + 1
    grant.constraints["used_count"] = used_count
    if used_count >= int(grant.constraints.get("max_uses", 1)):
        grant.status = JitGrantStatus.USED


class WorkflowService:
    def __init__(
        self,
        *,
        store: WorkflowStore | None = None,
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
        await self._publish("workflow.request.created", request, actor=actor)
        await self.store.commit()
        return request

    async def submit_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        tenant_id: str,
        actor: dict[str, Any] | None = None,
    ) -> WorkflowRequestRecord:
        request = await self._get_request_for_tenant(request_id, tenant_id)
        if request.requester_id != actor_id:
            raise PermissionError("WORKFLOW_REQUESTER_MISMATCH")
        self._transition(request, WorkflowRequestStatus.PENDING)
        request.submitted_at = self.now()
        await self.store.save_request(request)
        await self._publish(
            "workflow.request.submitted",
            request,
            actor=actor or {"id": actor_id, "tenant_id": tenant_id},
        )
        await self.store.commit()
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
            constraints={
                "subject_id": request.requester_id,
                "asset_id": request.asset_id,
                "account_id": request.account_id,
                "protocol": request.protocol,
                "action": request.action,
                "usage": "single-use",
                "max_uses": 1,
                "used_count": 0,
            },
        )
        request.decided_at = now
        request.expires_at = grant.expires_at
        request.decision_reason = decision_reason
        request.approver_id = str(actor["id"])
        request.approver_username = str(actor.get("username", ""))
        request.grant_id = grant.id
        await self.store.save_grant(grant)
        await self.store.save_request(request)
        await self._publish(
            "workflow.request.approved",
            request,
            decision_reason=decision_reason,
            actor=actor,
        )
        await self._publish_grant("jit.grant.issued", grant, request, actor=actor)
        await self.store.commit()
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
        await self._publish(
            "workflow.request.rejected",
            request,
            decision_reason=decision_reason,
            actor=actor,
        )
        await self.store.commit()
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
                await self._publish_grant("jit.grant.revoked", grant, request, actor=actor)
        await self.store.save_request(request)
        await self._publish("workflow.request.revoked", request, decision_reason=reason, actor=actor)
        await self.store.commit()
        return request

    async def list_requests(
        self,
        *,
        actor: dict[str, Any],
    ) -> list[WorkflowRequestRecord]:
        requester_id = None if self._can_view_tenant_requests(actor) else str(actor["id"])
        return await self.store.list_requests(
            tenant_id=str(actor.get("tenant_id", "default")),
            requester_id=requester_id,
        )

    async def get_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestRecord | None:
        request = await self.store.get_request(request_id)
        if request is None or request.tenant_id != tenant_id:
            return None
        return request

    async def get_request_for_actor(
        self,
        request_id: str,
        *,
        actor: dict[str, Any],
    ) -> WorkflowRequestRecord | None:
        request = await self.get_request(
            request_id,
            tenant_id=str(actor.get("tenant_id", "default")),
        )
        if request is None:
            return None
        if self._can_view_tenant_requests(actor) or request.requester_id == str(actor["id"]):
            return request
        return None

    async def get_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantRecord | None:
        grant = await self.store.get_grant(grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            return None
        return grant

    async def list_active_grants(self, *, actor: dict[str, Any]) -> list[JitGrantRecord]:
        subject_id = None if self._can_view_tenant_grants(actor) else str(actor["id"])
        return await self.store.list_active_grants(
            tenant_id=str(actor.get("tenant_id", "default")),
            now=self.now(),
            subject_id=subject_id,
        )

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
        grant = await self.store.get_grant(jit_grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            raise PermissionError("JIT_GRANT_NOT_FOUND")
        _validate_grant_for_session(
            grant,
            subject_id=subject_id,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            now=now,
        )
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
        grant = await self.store.reserve_grant_for_session(
            jit_grant_id=jit_grant_id,
            subject_id=grant.subject_id,
            tenant_id=grant.tenant_id,
            asset_id=grant.asset_id,
            account_id=grant.account_id,
            protocol=grant.protocol,
            action=grant.action,
            now=self.now(),
        )
        await self.store.commit()
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

    def _can_view_tenant_requests(self, actor: dict[str, Any]) -> bool:
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
        actor: dict[str, Any] | None = None,
    ) -> None:
        await self.audit_sink.publish(
            {
                "id": uuid.uuid4().hex,
                "type": event_type,
                "actor_id": str((actor or {}).get("id") or request.requester_id),
                "actor_username": str((actor or {}).get("username") or request.requester_username),
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
        actor: dict[str, Any] | None = None,
    ) -> None:
        await self.audit_sink.publish(
            {
                "id": uuid.uuid4().hex,
                "type": event_type,
                "actor_id": str((actor or {}).get("id") or grant.subject_id),
                "actor_username": str((actor or {}).get("username") or request.requester_username),
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
