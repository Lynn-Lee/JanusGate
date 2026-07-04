"""Workflow/JIT SQLAlchemy persistence models."""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkflowRequestStatus(StrEnum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    revoked = "revoked"


class JitGrantStatus(StrEnum):
    active = "active"
    used = "used"
    expired = "expired"
    revoked = "revoked"


class ApproverMode(StrEnum):
    named_user = "named_user"
    manager = "manager"


class WorkflowRequestModel(Base):
    """JIT access request persisted for approval and audit recovery."""

    __tablename__ = "workflow_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requester_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requester_username: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[WorkflowRequestStatus] = mapped_column(
        String(20), nullable=False, default=WorkflowRequestStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    approver_id: Mapped[str] = mapped_column(String(64), default="")
    approver_username: Mapped[str] = mapped_column(String(100), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class JitGrantModel(Base):
    """Temporary least-privilege grant produced by an approved workflow request."""

    __tablename__ = "jit_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[JitGrantStatus] = mapped_column(
        String(20), nullable=False, default=JitGrantStatus.active
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_session_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    constraints_json: Mapped[str] = mapped_column(Text, default="{}")


class ApprovalPolicyModel(Base):
    """Persisted approval policy selector and approver requirements."""

    __tablename__ = "approval_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    resource_selector_json: Mapped[str] = mapped_column(Text, default="{}")
    action_selector: Mapped[str] = mapped_column(String(120), nullable=False)
    approver_subject_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    approver_mode: Mapped[ApproverMode] = mapped_column(
        String(20), nullable=False, default=ApproverMode.named_user
    )
    require_mfa_for_requester: Mapped[bool] = mapped_column(Boolean, default=False)
    require_mfa_for_approver: Mapped[bool] = mapped_column(Boolean, default=True)
    max_grant_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    allow_self_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
