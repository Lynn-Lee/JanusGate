"""Workflow/JIT request API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.service import audit_service
from app.api.sessions.routes import get_session_revoker
from app.api.workflows.schemas import (
    ApprovalPolicyCreate,
    ApprovalPolicyListResponse,
    ApprovalPolicyResponse,
    JitGrantListResponse,
    JitGrantResponse,
    WorkflowDecisionRequest,
    WorkflowRejectRequest,
    WorkflowRequestCreate,
    WorkflowRequestListResponse,
    WorkflowRequestResponse,
    WorkflowRevokeRequest,
)
from app.api.workflows.service import SQLAlchemyWorkflowStore, WorkflowService
from app.core.database import get_db
from app.core.deps import current_user
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import PolicyDecisionRequest, PolicyDecisionResponse
from app.workflows.audit import WorkflowAuditSink
from app.workflows.repository import SQLAlchemyWorkflowRepository

router = APIRouter(prefix="/workflows", tags=["Workflow/JIT"])

_workflow_audit_sink = WorkflowAuditSink(audit_service)


def get_workflow_service(db: AsyncSession = Depends(get_db)) -> WorkflowService:
    return WorkflowService(
        store=SQLAlchemyWorkflowStore(db),
        audit_sink=_workflow_audit_sink,
        session_revoker=get_session_revoker(),
    )


@router.get("/approval-policies", response_model=ApprovalPolicyListResponse)
async def list_approval_policies(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ApprovalPolicyListResponse:
    _require_workflow_admin_permission(user)
    repo = SQLAlchemyWorkflowRepository(db)
    policies = await repo.list_approval_policies(tenant_id=str(user.get("tenant_id", "default")))
    items = [ApprovalPolicyResponse.from_model(policy) for policy in policies]
    return ApprovalPolicyListResponse(items=items, total=len(items))


@router.post(
    "/approval-policies",
    response_model=ApprovalPolicyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_approval_policy(
    data: ApprovalPolicyCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ApprovalPolicyResponse:
    _require_workflow_admin_permission(user)
    repo = SQLAlchemyWorkflowRepository(db)
    policy = await repo.create_approval_policy(
        tenant_id=str(user.get("tenant_id", "default")),
        resource_selector=data.resource_selector,
        action_selector=data.action_selector,
        approver_subject_ids=data.approver_subject_ids,
        approver_mode=data.approver_mode,
        require_mfa_for_requester=data.require_mfa_for_requester,
        require_mfa_for_approver=data.require_mfa_for_approver,
        max_grant_ttl_seconds=data.max_grant_ttl_seconds,
        allow_self_approval=data.allow_self_approval,
        risk_level=data.risk_level,
    )
    await db.commit()
    await db.refresh(policy)
    return ApprovalPolicyResponse.from_model(policy)


@router.post(
    "/approval-policies/{policy_id}/versions",
    response_model=ApprovalPolicyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_approval_policy_version(
    policy_id: str,
    data: ApprovalPolicyCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ApprovalPolicyResponse:
    _require_workflow_admin_permission(user)
    repo = SQLAlchemyWorkflowRepository(db)
    try:
        policy = await repo.create_approval_policy_version(
            tenant_id=str(user.get("tenant_id", "default")),
            policy_id=policy_id,
            resource_selector=data.resource_selector,
            action_selector=data.action_selector,
            approver_subject_ids=data.approver_subject_ids,
            approver_mode=data.approver_mode,
            require_mfa_for_requester=data.require_mfa_for_requester,
            require_mfa_for_approver=data.require_mfa_for_approver,
            max_grant_ttl_seconds=data.max_grant_ttl_seconds,
            allow_self_approval=data.allow_self_approval,
            risk_level=data.risk_level,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APPROVAL_POLICY_NOT_FOUND") from exc
    await db.commit()
    await db.refresh(policy)
    return ApprovalPolicyResponse.from_model(policy)


@router.post(
    "/approval-policies/{policy_id}/rollback",
    response_model=ApprovalPolicyResponse,
)
async def rollback_approval_policy(
    policy_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ApprovalPolicyResponse:
    _require_workflow_admin_permission(user)
    repo = SQLAlchemyWorkflowRepository(db)
    try:
        policy = await repo.rollback_approval_policy(
            tenant_id=str(user.get("tenant_id", "default")),
            policy_id=policy_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APPROVAL_POLICY_NOT_FOUND") from exc
    await db.commit()
    await db.refresh(policy)
    return ApprovalPolicyResponse.from_model(policy)


@router.post("/approval-policies/simulate", response_model=PolicyDecisionResponse)
async def simulate_approval_policy(
    data: PolicyDecisionRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> PolicyDecisionResponse:
    _require_workflow_admin_permission(user)
    tenant_id = str(user.get("tenant_id", "default"))
    repo = SQLAlchemyWorkflowRepository(db)
    policies = await repo.list_approval_policies(tenant_id=tenant_id)
    request = data.model_copy(
        update={
            "subject": data.subject.model_copy(update={"tenant_id": tenant_id}),
            "resource": data.resource.model_copy(update={"tenant_id": tenant_id}),
        },
    )
    return PolicyDecisionService(rules=[], approval_policies=policies).evaluate(request)


@router.post(
    "/requests",
    response_model=WorkflowRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_request(
    data: WorkflowRequestCreate,
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestResponse:
    request = await service.create_request(
        actor=user,
        asset_id=data.asset_id,
        account_id=data.account_id,
        protocol=data.protocol,
        action=data.action,
        reason=data.reason,
        requested_ttl_seconds=data.requested_ttl_seconds,
        metadata=data.metadata,
    )
    return WorkflowRequestResponse.from_record(request)


@router.get("/requests", response_model=WorkflowRequestListResponse)
async def list_workflow_requests(
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestListResponse:
    records = await service.list_requests(actor=user)
    items = [WorkflowRequestResponse.from_record(record) for record in records]
    return WorkflowRequestListResponse(items=items, total=len(items))


@router.get("/requests/{request_id}", response_model=WorkflowRequestResponse)
async def get_workflow_request(
    request_id: str,
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestResponse:
    record = await service.get_request_for_actor(
        request_id,
        actor=user,
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WORKFLOW_REQUEST_NOT_FOUND")
    return WorkflowRequestResponse.from_record(record)


@router.post("/requests/{request_id}/submit", response_model=WorkflowRequestResponse)
async def submit_workflow_request(
    request_id: str,
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestResponse:
    try:
        record = await service.submit_request(
            request_id,
            actor_id=str(user["id"]),
            tenant_id=str(user.get("tenant_id", "default")),
            actor=user,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return WorkflowRequestResponse.from_record(record)


@router.post("/requests/{request_id}/approve", response_model=WorkflowRequestResponse)
async def approve_workflow_request(
    request_id: str,
    data: WorkflowDecisionRequest,
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestResponse:
    try:
        record = await service.approve_request(
            request_id,
            actor=user,
            decision_reason=data.decision_reason,
            grant_ttl_seconds=data.grant_ttl_seconds,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return WorkflowRequestResponse.from_record(record)


@router.post("/requests/{request_id}/reject", response_model=WorkflowRequestResponse)
async def reject_workflow_request(
    request_id: str,
    data: WorkflowRejectRequest,
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestResponse:
    try:
        record = await service.reject_request(
            request_id,
            actor=user,
            decision_reason=data.decision_reason,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return WorkflowRequestResponse.from_record(record)


@router.post("/requests/{request_id}/revoke", response_model=WorkflowRequestResponse)
async def revoke_workflow_request(
    request_id: str,
    data: WorkflowRevokeRequest,
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRequestResponse:
    try:
        record = await service.revoke_request(request_id, actor=user, reason=data.reason)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise _value_error_to_http(exc) from exc
    return WorkflowRequestResponse.from_record(record)


@router.get("/grants/active", response_model=JitGrantListResponse)
async def list_active_jit_grants(
    user: dict[str, Any] = Depends(current_user),
    service: WorkflowService = Depends(get_workflow_service),
) -> JitGrantListResponse:
    records = await service.list_active_grants(actor=user)
    items = [JitGrantResponse.from_record(record) for record in records]
    return JitGrantListResponse(items=items, total=len(items))


def _value_error_to_http(exc: ValueError) -> HTTPException:
    detail = str(exc)
    status_code = (
        status.HTTP_404_NOT_FOUND
        if detail in {"WORKFLOW_REQUEST_NOT_FOUND"}
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=status_code, detail=detail)


def _require_workflow_admin_permission(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or "workflow:admin" in permissions:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="缺少权限: workflow:admin")
