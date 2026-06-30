"""Workflow/JIT request API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.workflows.schemas import (
    JitGrantListResponse,
    JitGrantResponse,
    WorkflowDecisionRequest,
    WorkflowRejectRequest,
    WorkflowRequestCreate,
    WorkflowRequestListResponse,
    WorkflowRequestResponse,
    WorkflowRevokeRequest,
)
from app.api.workflows.service import WorkflowService
from app.core.deps import current_user

router = APIRouter(prefix="/workflows", tags=["Workflow/JIT"])

_workflow_service = WorkflowService()


def get_workflow_service() -> WorkflowService:
    return _workflow_service


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
