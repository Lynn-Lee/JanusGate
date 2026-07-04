"""Workflow/JIT API schemas."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.api.workflows.service import JitGrantRecord, WorkflowRequestRecord, WorkflowRequestStatus
from app.models.workflow import ApprovalPolicyModel, ApproverMode


class ApprovalPolicyCreate(BaseModel):
    resource_selector: dict[str, Any] = Field(default_factory=dict)
    action_selector: str = Field(min_length=1, max_length=120)
    approver_subject_ids: list[str] = Field(min_length=1)
    approver_mode: ApproverMode = ApproverMode.named_user
    require_mfa_for_requester: bool = False
    require_mfa_for_approver: bool = True
    max_grant_ttl_seconds: int = Field(default=1800, gt=0, le=86_400)
    allow_self_approval: bool = False
    risk_level: str = Field(default="medium", min_length=1, max_length=20)


class ApprovalPolicyResponse(BaseModel):
    id: str
    tenant_id: str
    policy_family_id: str
    version: int
    is_active: bool
    resource_selector: dict[str, Any]
    action_selector: str
    approver_subject_ids: list[str]
    approver_mode: ApproverMode
    require_mfa_for_requester: bool
    require_mfa_for_approver: bool
    max_grant_ttl_seconds: int
    allow_self_approval: bool
    risk_level: str
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_model(cls, policy: ApprovalPolicyModel) -> ApprovalPolicyResponse:
        return cls(
            id=policy.id,
            tenant_id=policy.tenant_id,
            policy_family_id=policy.policy_family_id,
            version=policy.version,
            is_active=policy.is_active,
            resource_selector=json.loads(policy.resource_selector_json),
            action_selector=policy.action_selector,
            approver_subject_ids=json.loads(policy.approver_subject_ids_json),
            approver_mode=policy.approver_mode,
            require_mfa_for_requester=policy.require_mfa_for_requester,
            require_mfa_for_approver=policy.require_mfa_for_approver,
            max_grant_ttl_seconds=policy.max_grant_ttl_seconds,
            allow_self_approval=policy.allow_self_approval,
            risk_level=policy.risk_level,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
        )


class ApprovalPolicyListResponse(BaseModel):
    items: list[ApprovalPolicyResponse]
    total: int


class WorkflowRequestCreate(BaseModel):
    asset_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    protocol: str = Field(min_length=1, max_length=32)
    action: str = Field(default="session.connect", min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)
    requested_ttl_seconds: int = Field(gt=0, le=86_400)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowDecisionRequest(BaseModel):
    decision_reason: str = Field(min_length=1, max_length=500)
    grant_ttl_seconds: int = Field(default=1800, gt=0, le=86_400)


class WorkflowRejectRequest(BaseModel):
    decision_reason: str = Field(min_length=1, max_length=500)


class WorkflowRevokeRequest(BaseModel):
    reason: str = Field(default="revoked", min_length=1, max_length=500)


class WorkflowRequestResponse(BaseModel):
    id: str
    tenant_id: str
    requester_id: str
    requester_username: str
    asset_id: str
    account_id: str
    protocol: str
    action: str
    reason: str
    requested_ttl_seconds: int
    status: WorkflowRequestStatus
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    decision_reason: str
    approver_id: str
    approver_username: str
    grant_id: str
    metadata: dict[str, Any]

    @classmethod
    def from_record(cls, record: WorkflowRequestRecord) -> WorkflowRequestResponse:
        return cls(**record.model_dump())


class WorkflowRequestListResponse(BaseModel):
    items: list[WorkflowRequestResponse]
    total: int


class JitGrantResponse(BaseModel):
    id: str
    tenant_id: str
    workflow_request_id: str
    subject_id: str
    asset_id: str
    account_id: str
    protocol: str
    action: str
    status: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    max_session_ttl_seconds: int
    constraints: dict[str, Any]

    @classmethod
    def from_record(cls, record: JitGrantRecord) -> JitGrantResponse:
        return cls(**record.model_dump())


class JitGrantListResponse(BaseModel):
    items: list[JitGrantResponse]
    total: int
