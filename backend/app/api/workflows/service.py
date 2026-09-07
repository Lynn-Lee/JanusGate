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
    TicketStepModel,
    WorkflowRequestModel,
)
from app.models.workflow import (
    JitGrantStatus as SQLAlchemyJitGrantStatus,
)
from app.models.workflow import (
    TicketStepStatus as SQLAlchemyTicketStepStatus,
)
from app.models.workflow import (
    WorkflowRequestStatus as SQLAlchemyWorkflowRequestStatus,
)
from app.workflows.command_review import (
    COMMAND_ALLOW_ACTION,
    COMMAND_REVIEW_ACTION,
    COMMAND_REVIEW_TTL_SECONDS,
    build_command_review_metadata,
    command_from_request,
    is_command_review_request,
)
from app.workflows.ticket_flows import TicketFlowSnapshot, step_to_dict

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


class TicketStepRecord(BaseModel):
    id: str = ""
    level: int
    status: str = "not_started"
    approver_user_ids: list[str] = Field(default_factory=list)
    decided_by_id: str = ""
    decided_by_username: str = ""
    decided_at: datetime | None = None
    decision_reason: str = ""


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
    ticket_flow_id: str = ""
    current_level: int = 0
    total_levels: int = 0
    steps: list[TicketStepRecord] = Field(default_factory=list)


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
                ticket_flow_id=request.ticket_flow_id,
                current_level=request.current_level,
                total_levels=request.total_levels,
            )
            self._session.add(model)
        self._apply_request_record(model, request)
        await self._sync_steps(request)
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
        steps = await self._load_steps(model.id, model.tenant_id)
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
            ticket_flow_id=getattr(model, "ticket_flow_id", "") or "",
            current_level=int(getattr(model, "current_level", 0) or 0),
            total_levels=int(getattr(model, "total_levels", 0) or 0),
            steps=steps,
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
        model.ticket_flow_id = request.ticket_flow_id
        model.current_level = request.current_level
        model.total_levels = request.total_levels

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

    async def _load_steps(self, request_id: str, tenant_id: str) -> list[TicketStepRecord]:
        result = await self._session.execute(
            select(TicketStepModel)
            .where(
                TicketStepModel.workflow_request_id == request_id,
                TicketStepModel.tenant_id == tenant_id,
            )
            .order_by(TicketStepModel.level.asc())
        )
        steps: list[TicketStepRecord] = []
        for model in result.scalars().all():
            payload = step_to_dict(model)
            steps.append(TicketStepRecord(**payload))
        return steps

    async def _sync_steps(self, request: WorkflowRequestRecord) -> None:
        if not request.steps and not request.ticket_flow_id:
            return
        existing = await self._session.execute(
            select(TicketStepModel).where(
                TicketStepModel.workflow_request_id == request.id,
                TicketStepModel.tenant_id == request.tenant_id,
            )
        )
        by_level = {int(step.level): step for step in existing.scalars().all()}
        for step in request.steps:
            model = by_level.get(step.level)
            if model is None:
                model = TicketStepModel(
                    id=step.id or f"ts_{uuid.uuid4().hex}",
                    tenant_id=request.tenant_id,
                    workflow_request_id=request.id,
                    ticket_flow_id=request.ticket_flow_id,
                    level=step.level,
                    status=SQLAlchemyTicketStepStatus(step.status),
                    approver_user_ids_json=json.dumps(step.approver_user_ids, sort_keys=True),
                    decided_by_id=step.decided_by_id,
                    decided_by_username=step.decided_by_username,
                    decided_at=step.decided_at,
                    decision_reason=step.decision_reason,
                )
                self._session.add(model)
            else:
                model.status = SQLAlchemyTicketStepStatus(step.status)
                model.approver_user_ids_json = json.dumps(step.approver_user_ids, sort_keys=True)
                model.decided_by_id = step.decided_by_id
                model.decided_by_username = step.decided_by_username
                model.decided_at = step.decided_at
                model.decision_reason = step.decision_reason
                model.ticket_flow_id = request.ticket_flow_id



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
        ticket_flow_loader: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store or InMemoryWorkflowStore()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.session_revoker = session_revoker or NoopSessionRevoker()
        self.now = now or (lambda: datetime.now(UTC))
        self.request_id_factory = request_id_factory or (lambda: f"wr_{uuid.uuid4().hex}")
        self.grant_id_factory = grant_id_factory or (lambda: f"jg_{uuid.uuid4().hex}")
        self.ticket_flow_loader = ticket_flow_loader
        self._enabled_flows: dict[str, TicketFlowSnapshot] = {}

    def set_enabled_ticket_flow(
        self, tenant_id: str, flow: TicketFlowSnapshot | None, *, flow_type: str = "asset_grant"
    ) -> None:
        """Test helper: bind an enabled TicketFlow for a tenant + type."""

        key = f"{tenant_id}:{flow_type}"
        if flow is None:
            self._enabled_flows.pop(key, None)
            # legacy key used by older tests
            if flow_type == "asset_grant":
                self._enabled_flows.pop(tenant_id, None)
        else:
            self._enabled_flows[key] = flow
            if flow_type == "asset_grant":
                self._enabled_flows[tenant_id] = flow

    async def _resolve_enabled_flow(
        self, tenant_id: str, *, flow_type: str = "asset_grant"
    ) -> TicketFlowSnapshot | None:
        if self.ticket_flow_loader is not None:
            try:
                flow = self.ticket_flow_loader(tenant_id, flow_type)
            except TypeError:
                # backward-compatible loaders that only accept tenant_id
                flow = self.ticket_flow_loader(tenant_id)
            if hasattr(flow, "__await__"):
                flow = await flow
            return flow
        return self._enabled_flows.get(f"{tenant_id}:{flow_type}") or (
            self._enabled_flows.get(tenant_id) if flow_type == "asset_grant" else None
        )

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
        flow = await self._resolve_enabled_flow(tenant_id, flow_type="asset_grant")
        if flow is not None and flow.levels:
            request.ticket_flow_id = flow.id
            request.total_levels = flow.level_count
            request.current_level = 1
            request.steps = [
                TicketStepRecord(
                    id=f"ts_{uuid.uuid4().hex}",
                    level=level.level,
                    status="pending" if level.level == 1 else "not_started",
                    approver_user_ids=list(level.approver_user_ids),
                )
                for level in flow.levels
            ]
        else:
            request.ticket_flow_id = ""
            request.current_level = 0
            request.total_levels = 0
            request.steps = []
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
        self._validate_ttl(grant_ttl_seconds)
        request = await self._get_request_for_tenant(request_id, str(actor.get("tenant_id", "default")))
        if request.requester_id == str(actor["id"]):
            raise PermissionError("SELF_APPROVAL_NOT_ALLOWED")
        if request.total_levels > 0 and request.ticket_flow_id:
            return await self._approve_multi_level(
                request,
                actor=actor,
                decision_reason=decision_reason,
                grant_ttl_seconds=grant_ttl_seconds,
            )
        if is_command_review_request(request):
            self._require_command_review_approver(request, actor)
            ttl = min(grant_ttl_seconds, COMMAND_REVIEW_TTL_SECONDS)
            return await self._finalize_approval(
                request,
                actor=actor,
                decision_reason=decision_reason,
                grant_ttl_seconds=ttl,
            )
        self._require_approve_permission(actor)
        return await self._finalize_approval(
            request,
            actor=actor,
            decision_reason=decision_reason,
            grant_ttl_seconds=grant_ttl_seconds,
        )

    async def _approve_multi_level(
        self,
        request: WorkflowRequestRecord,
        *,
        actor: dict[str, Any],
        decision_reason: str,
        grant_ttl_seconds: int,
    ) -> WorkflowRequestRecord:
        if request.status is not WorkflowRequestStatus.PENDING:
            raise ValueError(f"INVALID_WORKFLOW_TRANSITION:{request.status}->{WorkflowRequestStatus.APPROVED}")
        current = next((step for step in request.steps if step.level == request.current_level), None)
        if current is None or current.status != "pending":
            raise ValueError("TICKET_STEP_NOT_PENDING")
        actor_id = str(actor["id"])
        if actor_id not in {str(uid) for uid in current.approver_user_ids}:
            raise PermissionError("TICKET_STEP_APPROVER_MISMATCH")
        now = self.now()
        current.status = "approved"
        current.decided_by_id = actor_id
        current.decided_by_username = str(actor.get("username", ""))
        current.decided_at = now
        current.decision_reason = decision_reason
        request.approver_id = actor_id
        request.approver_username = str(actor.get("username", ""))
        request.decision_reason = decision_reason
        if request.current_level < request.total_levels:
            request.current_level += 1
            nxt = next((step for step in request.steps if step.level == request.current_level), None)
            if nxt is not None:
                nxt.status = "pending"
            await self.store.save_request(request)
            await self._publish(
                "workflow.request.step_approved",
                request,
                decision_reason=decision_reason,
                actor=actor,
            )
            await self.store.commit()
            return request
        return await self._finalize_approval(
            request,
            actor=actor,
            decision_reason=decision_reason,
            grant_ttl_seconds=grant_ttl_seconds,
        )

    async def _finalize_approval(
        self,
        request: WorkflowRequestRecord,
        *,
        actor: dict[str, Any],
        decision_reason: str,
        grant_ttl_seconds: int,
    ) -> WorkflowRequestRecord:
        self._transition(request, WorkflowRequestStatus.APPROVED)
        now = self.now()
        if is_command_review_request(request):
            command = command_from_request(request)
            session_id = str((request.metadata or {}).get("session_id") or "")
            ttl = min(grant_ttl_seconds, request.requested_ttl_seconds, COMMAND_REVIEW_TTL_SECONDS)
            grant = JitGrantRecord(
                id=self.grant_id_factory(),
                tenant_id=request.tenant_id,
                workflow_request_id=request.id,
                subject_id=request.requester_id,
                asset_id=request.asset_id,
                account_id=request.account_id,
                protocol=request.protocol,
                action=COMMAND_ALLOW_ACTION,
                issued_at=now,
                expires_at=now + timedelta(seconds=ttl),
                max_session_ttl_seconds=ttl,
                constraints={
                    "subject_id": request.requester_id,
                    "asset_id": request.asset_id,
                    "account_id": request.account_id,
                    "protocol": request.protocol,
                    "action": COMMAND_ALLOW_ACTION,
                    "command": command,
                    "session_id": session_id,
                    "usage": "single-use",
                    "max_uses": 1,
                    "used_count": 0,
                },
            )
        else:
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
        if not decision_reason:
            raise ValueError("DECISION_REASON_REQUIRED")
        request = await self._get_request_for_tenant(request_id, str(actor.get("tenant_id", "default")))
        if request.requester_id == str(actor["id"]):
            raise PermissionError("SELF_APPROVAL_NOT_ALLOWED")
        if request.total_levels > 0 and request.ticket_flow_id:
            if request.status is not WorkflowRequestStatus.PENDING:
                raise ValueError(
                    f"INVALID_WORKFLOW_TRANSITION:{request.status}->{WorkflowRequestStatus.REJECTED}"
                )
            current = next((step for step in request.steps if step.level == request.current_level), None)
            if current is None or current.status != "pending":
                raise ValueError("TICKET_STEP_NOT_PENDING")
            actor_id = str(actor["id"])
            if actor_id not in {str(uid) for uid in current.approver_user_ids}:
                raise PermissionError("TICKET_STEP_APPROVER_MISMATCH")
            now = self.now()
            current.status = "rejected"
            current.decided_by_id = actor_id
            current.decided_by_username = str(actor.get("username", ""))
            current.decided_at = now
            current.decision_reason = decision_reason
            for step in request.steps:
                if step.level > request.current_level and step.status == "not_started":
                    step.status = "not_started"
        else:
            if is_command_review_request(request):
                self._require_command_review_approver(request, actor)
            else:
                self._require_approve_permission(actor)
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

    def _require_command_review_approver(
        self, request: WorkflowRequestRecord, actor: dict[str, Any]
    ) -> None:
        """No-flow command_review: ACL reviewers, else admin-only."""

        reviewers = (request.metadata or {}).get("reviewer_subject_ids") or []
        if not isinstance(reviewers, list):
            reviewers = []
        actor_id = str(actor["id"])
        if reviewers:
            if actor_id not in {str(uid) for uid in reviewers}:
                raise PermissionError("COMMAND_REVIEW_APPROVER_MISMATCH")
            return
        if not self._has_any_permission(actor, {"workflow:admin", "admin"}):
            raise PermissionError("COMMAND_REVIEW_ADMIN_REQUIRED")

    async def ensure_command_review_ticket(
        self,
        *,
        actor: dict[str, Any],
        asset_id: str,
        account_id: str,
        protocol: str,
        command: str,
        session_id: str | None = None,
        reviewer_subject_ids: list[str] | None = None,
    ) -> WorkflowRequestRecord:
        """Create or reuse in-progress command_review ticket (Design A)."""

        tenant_id = str(actor.get("tenant_id", "default"))
        subject_id = str(actor["id"])
        existing = await self.find_pending_command_review(
            tenant_id=tenant_id,
            subject_id=subject_id,
            asset_id=asset_id,
            command=command,
        )
        if existing is not None:
            return existing

        metadata = build_command_review_metadata(
            command=command,
            session_id=session_id,
            reviewer_subject_ids=reviewer_subject_ids,
            protocol=protocol,
            account_id=account_id,
        )
        request = await self.create_request(
            actor=actor,
            asset_id=asset_id,
            account_id=account_id or "*",
            protocol=protocol or "ssh",
            action=COMMAND_REVIEW_ACTION,
            reason="命令复核",
            requested_ttl_seconds=COMMAND_REVIEW_TTL_SECONDS,
            metadata=metadata,
        )
        # inline pending transition with command_review flow (not asset_grant)
        request = await self._submit_command_review(request, actor=actor)
        return request

    async def _submit_command_review(
        self, request: WorkflowRequestRecord, *, actor: dict[str, Any]
    ) -> WorkflowRequestRecord:
        if request.status is not WorkflowRequestStatus.DRAFT:
            raise ValueError(f"INVALID_WORKFLOW_TRANSITION:{request.status}->{WorkflowRequestStatus.PENDING}")
        self._transition(request, WorkflowRequestStatus.PENDING)
        request.submitted_at = self.now()
        flow = await self._resolve_enabled_flow(request.tenant_id, flow_type="command_review")
        if flow is not None and flow.levels:
            request.ticket_flow_id = flow.id
            request.total_levels = flow.level_count
            request.current_level = 1
            request.steps = [
                TicketStepRecord(
                    id=f"ts_{uuid.uuid4().hex}",
                    level=level.level,
                    status="pending" if level.level == 1 else "not_started",
                    approver_user_ids=list(level.approver_user_ids),
                )
                for level in flow.levels
            ]
        else:
            request.ticket_flow_id = ""
            request.current_level = 0
            request.total_levels = 0
            request.steps = []
        await self.store.save_request(request)
        await self._publish("workflow.request.submitted", request, actor=actor)
        await self.store.commit()
        return request

    async def find_pending_command_review(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        asset_id: str,
        command: str,
    ) -> WorkflowRequestRecord | None:
        requests = await self.store.list_requests(tenant_id=tenant_id, requester_id=subject_id)
        for request in requests:
            if request.status is not WorkflowRequestStatus.PENDING:
                continue
            if request.action != COMMAND_REVIEW_ACTION:
                continue
            if request.asset_id != asset_id:
                continue
            if command_from_request(request) != command:
                continue
            return request
        return None

    async def consume_command_allow_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        asset_id: str,
        command: str,
        session_id: str | None = None,
    ) -> JitGrantRecord | None:
        """Consume one-shot command allow if present and still valid."""

        now = self.now()
        grants = await self.store.list_active_grants(
            tenant_id=tenant_id, now=now, subject_id=subject_id
        )
        for grant in grants:
            if grant.action != COMMAND_ALLOW_ACTION:
                continue
            if grant.asset_id != asset_id:
                continue
            if str(grant.constraints.get("command") or "") != command:
                continue
            grant_session = str(grant.constraints.get("session_id") or "")
            # session end = grant bound to a session that no longer matches
            if grant_session and session_id and grant_session != session_id:
                continue
            if grant_session and not session_id:
                continue
            _consume_grant(grant)
            await self.store.save_grant(grant)
            await self.store.commit()
            return grant
        return None

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
