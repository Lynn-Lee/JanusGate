"""Workflow/JIT repository interfaces and implementations."""
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import (
    ApprovalPolicyModel,
    ApproverMode,
    JitGrantModel,
    JitGrantStatus,
    WorkflowRequestModel,
    WorkflowRequestStatus,
)


def _new_request_id() -> str:
    return f"wr_{uuid4().hex}"


def _new_grant_id() -> str:
    return f"jg_{uuid4().hex}"


def _new_policy_id() -> str:
    return f"ap_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryWorkflowRepository:
    """Behavior-compatible repository used by unit tests and service fakes.

    Production code can provide an AsyncSession-backed implementation with the same method contract.
    """

    def __init__(self) -> None:
        self._requests: dict[str, WorkflowRequestModel] = {}
        self._grants: dict[str, JitGrantModel] = {}
        self._policies: dict[str, ApprovalPolicyModel] = {}

    def create_request(
        self,
        *,
        tenant_id: str,
        requester_id: str,
        requester_username: str,
        resource_type: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        reason: str,
        requested_ttl_seconds: int,
        metadata: dict[str, Any],
    ) -> WorkflowRequestModel:
        request = build_workflow_request(
            tenant_id=tenant_id,
            requester_id=requester_id,
            requester_username=requester_username,
            resource_type=resource_type,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            reason=reason,
            requested_ttl_seconds=requested_ttl_seconds,
            metadata=metadata,
        )
        self._requests[request.id] = request
        return request

    def get_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel | None:
        request = self._requests.get(request_id)
        if request is None or request.tenant_id != tenant_id:
            return None
        return request

    def list_requests(
        self, *, tenant_id: str, requester_id: str | None = None
    ) -> list[WorkflowRequestModel]:
        return [
            request
            for request in self._requests.values()
            if request.tenant_id == tenant_id
            and (requester_id is None or request.requester_id == requester_id)
        ]

    def submit_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel:
        request = self._require_request(request_id, tenant_id=tenant_id)
        mark_request_submitted(request)
        return request

    def approve_request(
        self,
        request_id: str,
        *,
        tenant_id: str,
        approver_id: str,
        approver_username: str,
        decision_reason: str,
        grant_ttl_seconds: int,
        max_session_ttl_seconds: int,
        constraints: dict[str, Any],
    ) -> JitGrantModel:
        request = self._require_request(request_id, tenant_id=tenant_id)
        grant = approve_request_and_build_grant(
            request,
            approver_id=approver_id,
            approver_username=approver_username,
            decision_reason=decision_reason,
            grant_ttl_seconds=grant_ttl_seconds,
            max_session_ttl_seconds=max_session_ttl_seconds,
            constraints=constraints,
        )
        self._grants[grant.id] = grant
        return grant

    def find_active_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantModel | None:
        for grant in self._grants.values():
            if grant_matches(
                grant,
                tenant_id=tenant_id,
                subject_id=subject_id,
                asset_id=asset_id,
                account_id=account_id,
                protocol=protocol,
                action=action,
                now=now,
            ):
                return grant
        return None

    def mark_grant_used(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = self._require_grant(grant_id, tenant_id=tenant_id)
        transition_grant(grant, JitGrantStatus.used)
        return grant

    def revoke_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = self._require_grant(grant_id, tenant_id=tenant_id)
        transition_grant(grant, JitGrantStatus.revoked)
        return grant

    def expire_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = self._require_grant(grant_id, tenant_id=tenant_id)
        transition_grant(grant, JitGrantStatus.expired)
        return grant

    def create_approval_policy(
        self,
        *,
        tenant_id: str,
        resource_selector: dict[str, Any],
        action_selector: str,
        approver_subject_ids: list[str],
        approver_mode: ApproverMode = ApproverMode.named_user,
        require_mfa_for_requester: bool = False,
        require_mfa_for_approver: bool = True,
        max_grant_ttl_seconds: int = 1800,
        allow_self_approval: bool = False,
        risk_level: str = "medium",
        rollout_percentage: int = 100,
        dsl_conditions: dict[str, Any] | None = None,
    ) -> ApprovalPolicyModel:
        policy = build_approval_policy(
            tenant_id=tenant_id,
            resource_selector=resource_selector,
            dsl_conditions=dsl_conditions or {},
            action_selector=action_selector,
            approver_subject_ids=approver_subject_ids,
            approver_mode=approver_mode,
            require_mfa_for_requester=require_mfa_for_requester,
            require_mfa_for_approver=require_mfa_for_approver,
            max_grant_ttl_seconds=max_grant_ttl_seconds,
            allow_self_approval=allow_self_approval,
            risk_level=risk_level,
            rollout_percentage=rollout_percentage,
        )
        self._policies[policy.id] = policy
        return policy

    def list_approval_policies(self, *, tenant_id: str) -> list[ApprovalPolicyModel]:
        return [
            policy
            for policy in self._policies.values()
            if policy.tenant_id == tenant_id and policy.is_active
        ]

    def create_approval_policy_version(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        resource_selector: dict[str, Any],
        action_selector: str,
        approver_subject_ids: list[str],
        approver_mode: ApproverMode = ApproverMode.named_user,
        require_mfa_for_requester: bool = False,
        require_mfa_for_approver: bool = True,
        max_grant_ttl_seconds: int = 1800,
        allow_self_approval: bool = False,
        risk_level: str = "medium",
        rollout_percentage: int = 100,
        dsl_conditions: dict[str, Any] | None = None,
    ) -> ApprovalPolicyModel:
        source = self._policies.get(policy_id)
        if source is None or source.tenant_id != tenant_id:
            raise ValueError("Approval policy not found")
        family_id = source.policy_family_id
        family = [
            policy
            for policy in self._policies.values()
            if policy.tenant_id == tenant_id and policy.policy_family_id == family_id
        ]
        for policy in family:
            policy.is_active = False
        version = build_approval_policy(
            tenant_id=tenant_id,
            resource_selector=resource_selector,
            dsl_conditions=dsl_conditions or {},
            action_selector=action_selector,
            approver_subject_ids=approver_subject_ids,
            approver_mode=approver_mode,
            require_mfa_for_requester=require_mfa_for_requester,
            require_mfa_for_approver=require_mfa_for_approver,
            max_grant_ttl_seconds=max_grant_ttl_seconds,
            allow_self_approval=allow_self_approval,
            risk_level=risk_level,
            rollout_percentage=rollout_percentage,
        )
        version.policy_family_id = family_id
        version.version = max((policy.version for policy in family), default=0) + 1
        self._policies[version.id] = version
        return version

    def rollback_approval_policy(
        self,
        *,
        tenant_id: str,
        policy_id: str,
    ) -> ApprovalPolicyModel:
        target = self._policies.get(policy_id)
        if target is None or target.tenant_id != tenant_id:
            raise ValueError("Approval policy not found")
        for policy in self._policies.values():
            if policy.tenant_id == tenant_id and policy.policy_family_id == target.policy_family_id:
                policy.is_active = policy.id == target.id
        return target

    def _require_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel:
        request = self.get_request(request_id, tenant_id=tenant_id)
        if request is None:
            raise ValueError("Workflow request not found")
        return request

    def _require_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = self._grants.get(grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            raise ValueError("JIT grant not found")
        return grant


class SQLAlchemyWorkflowRepository:
    """AsyncSession-backed Workflow/JIT repository for production persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_request(
        self,
        *,
        tenant_id: str,
        requester_id: str,
        requester_username: str,
        resource_type: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        reason: str,
        requested_ttl_seconds: int,
        metadata: dict[str, Any],
    ) -> WorkflowRequestModel:
        request = build_workflow_request(
            tenant_id=tenant_id,
            requester_id=requester_id,
            requester_username=requester_username,
            resource_type=resource_type,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            reason=reason,
            requested_ttl_seconds=requested_ttl_seconds,
            metadata=metadata,
        )
        self._session.add(request)
        await self._session.flush()
        return request

    async def get_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel | None:
        result = await self._session.execute(
            select(WorkflowRequestModel).where(
                WorkflowRequestModel.id == request_id,
                WorkflowRequestModel.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_requests(
        self, *, tenant_id: str, requester_id: str | None = None
    ) -> list[WorkflowRequestModel]:
        stmt = select(WorkflowRequestModel).where(WorkflowRequestModel.tenant_id == tenant_id)
        if requester_id is not None:
            stmt = stmt.where(WorkflowRequestModel.requester_id == requester_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def submit_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel:
        request = await self._require_request(request_id, tenant_id=tenant_id)
        mark_request_submitted(request)
        await self._session.flush()
        return request

    async def approve_request(
        self,
        request_id: str,
        *,
        tenant_id: str,
        approver_id: str,
        approver_username: str,
        decision_reason: str,
        grant_ttl_seconds: int,
        max_session_ttl_seconds: int,
        constraints: dict[str, Any],
    ) -> JitGrantModel:
        request = await self._require_request(request_id, tenant_id=tenant_id)
        grant = approve_request_and_build_grant(
            request,
            approver_id=approver_id,
            approver_username=approver_username,
            decision_reason=decision_reason,
            grant_ttl_seconds=grant_ttl_seconds,
            max_session_ttl_seconds=max_session_ttl_seconds,
            constraints=constraints,
        )
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def find_active_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantModel | None:
        result = await self._session.execute(
            select(JitGrantModel).where(
                JitGrantModel.tenant_id == tenant_id,
                JitGrantModel.subject_id == subject_id,
                JitGrantModel.asset_id == asset_id,
                JitGrantModel.account_id == account_id,
                JitGrantModel.protocol == protocol,
                JitGrantModel.action == action,
                JitGrantModel.status == JitGrantStatus.active,
                JitGrantModel.expires_at > now,
            )
        )
        return result.scalars().first()

    async def mark_grant_used(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = await self._require_grant(grant_id, tenant_id=tenant_id)
        transition_grant(grant, JitGrantStatus.used)
        await self._session.flush()
        return grant

    async def revoke_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = await self._require_grant(grant_id, tenant_id=tenant_id)
        transition_grant(grant, JitGrantStatus.revoked)
        await self._session.flush()
        return grant

    async def expire_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        grant = await self._require_grant(grant_id, tenant_id=tenant_id)
        transition_grant(grant, JitGrantStatus.expired)
        await self._session.flush()
        return grant

    async def create_approval_policy(
        self,
        *,
        tenant_id: str,
        resource_selector: dict[str, Any],
        action_selector: str,
        approver_subject_ids: list[str],
        approver_mode: ApproverMode = ApproverMode.named_user,
        require_mfa_for_requester: bool = False,
        require_mfa_for_approver: bool = True,
        max_grant_ttl_seconds: int = 1800,
        allow_self_approval: bool = False,
        risk_level: str = "medium",
        rollout_percentage: int = 100,
        dsl_conditions: dict[str, Any] | None = None,
    ) -> ApprovalPolicyModel:
        policy = build_approval_policy(
            tenant_id=tenant_id,
            resource_selector=resource_selector,
            dsl_conditions=dsl_conditions or {},
            action_selector=action_selector,
            approver_subject_ids=approver_subject_ids,
            approver_mode=approver_mode,
            require_mfa_for_requester=require_mfa_for_requester,
            require_mfa_for_approver=require_mfa_for_approver,
            max_grant_ttl_seconds=max_grant_ttl_seconds,
            allow_self_approval=allow_self_approval,
            risk_level=risk_level,
            rollout_percentage=rollout_percentage,
        )
        self._session.add(policy)
        await self._session.flush()
        return policy

    async def list_approval_policies(self, *, tenant_id: str) -> list[ApprovalPolicyModel]:
        result = await self._session.execute(
            select(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tenant_id == tenant_id,
                ApprovalPolicyModel.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def create_approval_policy_version(
        self,
        *,
        tenant_id: str,
        policy_id: str,
        resource_selector: dict[str, Any],
        action_selector: str,
        approver_subject_ids: list[str],
        approver_mode: ApproverMode = ApproverMode.named_user,
        require_mfa_for_requester: bool = False,
        require_mfa_for_approver: bool = True,
        max_grant_ttl_seconds: int = 1800,
        allow_self_approval: bool = False,
        risk_level: str = "medium",
        rollout_percentage: int = 100,
        dsl_conditions: dict[str, Any] | None = None,
    ) -> ApprovalPolicyModel:
        result = await self._session.execute(
            select(ApprovalPolicyModel).where(
                ApprovalPolicyModel.id == policy_id,
                ApprovalPolicyModel.tenant_id == tenant_id,
            )
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise ValueError("Approval policy not found")

        family_result = await self._session.execute(
            select(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tenant_id == tenant_id,
                ApprovalPolicyModel.policy_family_id == source.policy_family_id,
            )
        )
        family = list(family_result.scalars().all())
        for policy in family:
            policy.is_active = False

        version = build_approval_policy(
            tenant_id=tenant_id,
            resource_selector=resource_selector,
            dsl_conditions=dsl_conditions or {},
            action_selector=action_selector,
            approver_subject_ids=approver_subject_ids,
            approver_mode=approver_mode,
            require_mfa_for_requester=require_mfa_for_requester,
            require_mfa_for_approver=require_mfa_for_approver,
            max_grant_ttl_seconds=max_grant_ttl_seconds,
            allow_self_approval=allow_self_approval,
            risk_level=risk_level,
            rollout_percentage=rollout_percentage,
        )
        version.policy_family_id = source.policy_family_id
        version.version = max((policy.version for policy in family), default=0) + 1
        self._session.add(version)
        await self._session.flush()
        return version

    async def rollback_approval_policy(
        self,
        *,
        tenant_id: str,
        policy_id: str,
    ) -> ApprovalPolicyModel:
        result = await self._session.execute(
            select(ApprovalPolicyModel).where(
                ApprovalPolicyModel.id == policy_id,
                ApprovalPolicyModel.tenant_id == tenant_id,
            )
        )
        target = result.scalar_one_or_none()
        if target is None:
            raise ValueError("Approval policy not found")

        family_result = await self._session.execute(
            select(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tenant_id == tenant_id,
                ApprovalPolicyModel.policy_family_id == target.policy_family_id,
            )
        )
        for policy in family_result.scalars().all():
            policy.is_active = policy.id == target.id
        await self._session.flush()
        return target

    async def _require_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel:
        request = await self.get_request(request_id, tenant_id=tenant_id)
        if request is None:
            raise ValueError("Workflow request not found")
        return request

    async def _require_grant(self, grant_id: str, *, tenant_id: str) -> JitGrantModel:
        result = await self._session.execute(
            select(JitGrantModel).where(
                JitGrantModel.id == grant_id,
                JitGrantModel.tenant_id == tenant_id,
            )
        )
        grant = result.scalar_one_or_none()
        if grant is None:
            raise ValueError("JIT grant not found")
        return grant


def build_workflow_request(
    *,
    tenant_id: str,
    requester_id: str,
    requester_username: str,
    resource_type: str,
    asset_id: str,
    account_id: str,
    protocol: str,
    action: str,
    reason: str,
    requested_ttl_seconds: int,
    metadata: dict[str, Any],
) -> WorkflowRequestModel:
    return WorkflowRequestModel(
        id=_new_request_id(),
        tenant_id=tenant_id,
        requester_id=requester_id,
        requester_username=requester_username,
        resource_type=resource_type,
        asset_id=asset_id,
        account_id=account_id,
        protocol=protocol,
        action=action,
        reason=reason,
        requested_ttl_seconds=requested_ttl_seconds,
        status=WorkflowRequestStatus.draft,
        created_at=_utcnow(),
        metadata_json=json.dumps(metadata, sort_keys=True),
    )


def mark_request_submitted(request: WorkflowRequestModel) -> None:
    if request.status != WorkflowRequestStatus.draft:
        raise ValueError("Only draft workflow requests can be submitted")
    request.status = WorkflowRequestStatus.pending
    request.submitted_at = _utcnow()


def approve_request_and_build_grant(
    request: WorkflowRequestModel,
    *,
    approver_id: str,
    approver_username: str,
    decision_reason: str,
    grant_ttl_seconds: int,
    max_session_ttl_seconds: int,
    constraints: dict[str, Any],
) -> JitGrantModel:
    if request.status != WorkflowRequestStatus.pending:
        raise ValueError("Only pending workflow requests can be approved")
    now = _utcnow()
    request.status = WorkflowRequestStatus.approved
    request.decided_at = now
    request.decision_reason = decision_reason
    request.approver_id = approver_id
    request.approver_username = approver_username
    request.expires_at = now + timedelta(seconds=grant_ttl_seconds)
    return JitGrantModel(
        id=_new_grant_id(),
        tenant_id=request.tenant_id,
        workflow_request_id=request.id,
        subject_id=request.requester_id,
        asset_id=request.asset_id,
        account_id=request.account_id,
        protocol=request.protocol,
        action=request.action,
        status=JitGrantStatus.active,
        issued_at=now,
        expires_at=request.expires_at,
        max_session_ttl_seconds=max_session_ttl_seconds,
        constraints_json=json.dumps(constraints, sort_keys=True),
    )


def grant_matches(
    grant: JitGrantModel,
    *,
    tenant_id: str,
    subject_id: str,
    asset_id: str,
    account_id: str,
    protocol: str,
    action: str,
    now: datetime,
) -> bool:
    return (
        grant.tenant_id == tenant_id
        and grant.subject_id == subject_id
        and grant.asset_id == asset_id
        and grant.account_id == account_id
        and grant.protocol == protocol
        and grant.action == action
        and grant.status == JitGrantStatus.active
        and grant.expires_at > now
    )


def transition_grant(grant: JitGrantModel, target_status: JitGrantStatus) -> None:
    if grant.status not in {JitGrantStatus.active, JitGrantStatus.used}:
        raise ValueError("Only active or used JIT grants can transition")
    if target_status == JitGrantStatus.used and grant.status != JitGrantStatus.active:
        raise ValueError("Only active JIT grants can be marked used")
    grant.status = target_status
    if target_status in {JitGrantStatus.expired, JitGrantStatus.revoked}:
        grant.revoked_at = _utcnow() if target_status == JitGrantStatus.revoked else grant.revoked_at


def build_approval_policy(
    *,
    tenant_id: str,
    resource_selector: dict[str, Any],
    action_selector: str,
    approver_subject_ids: list[str],
    approver_mode: ApproverMode,
    require_mfa_for_requester: bool,
    require_mfa_for_approver: bool,
    max_grant_ttl_seconds: int,
    allow_self_approval: bool,
    risk_level: str,
    rollout_percentage: int = 100,
    dsl_conditions: dict[str, Any] | None = None,
) -> ApprovalPolicyModel:
    policy_id = _new_policy_id()
    return ApprovalPolicyModel(
        id=policy_id,
        tenant_id=tenant_id,
        policy_family_id=policy_id,
        version=1,
        is_active=True,
        rollout_percentage=rollout_percentage,
        resource_selector_json=json.dumps(resource_selector, sort_keys=True),
        dsl_conditions_json=json.dumps(dsl_conditions or {}, sort_keys=True),
        action_selector=action_selector,
        approver_subject_ids_json=json.dumps(approver_subject_ids, sort_keys=True),
        approver_mode=approver_mode,
        require_mfa_for_requester=require_mfa_for_requester,
        require_mfa_for_approver=require_mfa_for_approver,
        max_grant_ttl_seconds=max_grant_ttl_seconds,
        allow_self_approval=allow_self_approval,
        risk_level=risk_level,
    )
