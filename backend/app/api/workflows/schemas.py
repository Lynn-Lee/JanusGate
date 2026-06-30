"""Workflow/JIT API schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.api.workflows.service import JitGrantRecord, WorkflowRequestRecord, WorkflowRequestStatus


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
