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


class CommandFilterEffect(StrEnum):
    """命令过滤判定归一化后的三态效果。

    区别于会话级 :class:`PolicyDecision` 的二态：命令过滤在**已授权会话**之上叠加，
    故除放行/拒绝外还有「需复核」（review）这一挂起态。
    """

    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"


class CommandDecisionRequest(BaseModel):
    """命令过滤判定的输入契约（会话内逐条命令评估）。"""

    subject: SubjectRef
    resource: ResourceRef
    account_id: str
    command: str
    context: dict[str, Any] = Field(default_factory=dict)


class CommandDecisionResponse(BaseModel):
    """命令过滤判定的响应，返回给连接器与审计管线。

    :ivar effect: 归一化三态效果（放行 / 拒绝 / 需复核）。
    :ivar action: 命中 ACL 的原始动作（如 ``notify_and_warn``）；无命中时为 ``accept``。
    :ivar matched_acl_id: 命中的命令过滤 ACL ID；无命中为空串。
    :ivar matched_command_group_id: 命中的命令组 ID；无命中为空串。
    :ivar reviewer_subject_ids: ``review`` 动作的复核人主体 ID。
    """

    effect: CommandFilterEffect
    action: str
    reason_code: str
    matched_acl_id: str = ""
    matched_command_group_id: str = ""
    reviewer_subject_ids: list[str] = Field(default_factory=list)
    explain_trace: list[str]
    obligations: dict[str, Any] = Field(default_factory=dict)
    audit_event_id: str
