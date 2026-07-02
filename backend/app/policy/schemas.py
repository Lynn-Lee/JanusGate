"""Schemas for JanusGate policy decisions."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PolicyDecision(StrEnum):
    """Final policy decision values."""

    ALLOW = "allow"
    DENY = "deny"


class SubjectRef(BaseModel):
    """Actor requesting an operation."""

    id: str
    type: str = "user"
    tenant_id: str = "default"
    roles: list[str] = Field(default_factory=list)


class ResourceRef(BaseModel):
    """Target resource for an operation."""

    id: str
    type: str
    tenant_id: str = "default"
    organization_id: str = ""
    team_id: str = ""
    project_id: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class ApprovalState(BaseModel):
    """Approval/JIT state attached to a decision request."""

    status: Literal["not_required", "pending", "approved", "denied", "revoked", "expired"] = (
        "not_required"
    )
    expires_at: datetime | None = None
    grant_id: str = ""
    workflow_request_id: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)

    def is_approved_now(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if self.status != "approved":
            return False
        if self.expires_at is None:
            return True
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > current

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        current = now or datetime.now(UTC)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= current


class PolicyDecisionRequest(BaseModel):
    """Input contract for PolicyDecisionService."""

    subject: SubjectRef
    action: str
    resource: ResourceRef
    context: dict[str, Any] = Field(default_factory=dict)
    risk_signals: dict[str, Any] = Field(default_factory=dict)
    mfa_verified: bool = False
    approval: ApprovalState | None = None
    connector_trusted: bool = False


class PolicyRule(BaseModel):
    """Minimal explicit allow policy rule."""

    id: str
    subject_ids: list[str]
    actions: list[str]
    resource_ids: list[str]
    tenant_id: str = "default"
    require_mfa: bool = False
    require_approval: bool = False
    organization_ids: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    max_session_ttl_seconds: int = 900
    approval_use_type: Literal["single-use", "limited-use"] = "single-use"
    approval_max_uses: int = 1

    def matches(self, request: PolicyDecisionRequest) -> bool:
        return (
            (self.tenant_id == "*" or request.subject.tenant_id == self.tenant_id)
            and (self.tenant_id == "*" or request.resource.tenant_id == self.tenant_id)
            and ("*" in self.subject_ids or request.subject.id in self.subject_ids)
            and request.action in self.actions
            and ("*" in self.resource_ids or request.resource.id in self.resource_ids)
            and self._scope_matches(self.organization_ids, request.resource.organization_id)
            and self._scope_matches(self.team_ids, request.resource.team_id)
            and self._scope_matches(self.project_ids, request.resource.project_id)
        )

    def _scope_matches(self, allowed_ids: list[str], resource_scope_id: str) -> bool:
        if not allowed_ids:
            return True
        return "*" in allowed_ids or resource_scope_id in allowed_ids


class PolicyDecisionResponse(BaseModel):
    """Decision response returned to callers and audit pipeline."""

    decision: PolicyDecision
    reason_code: str
    explain_trace: list[str]
    obligations: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = 0
    audit_event_id: str
