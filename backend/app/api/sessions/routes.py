"""Session Gateway API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.service import audit_service
from app.api.sessions.schemas import SessionCloseRequest, SessionCreateRequest, SessionResponse
from app.api.sessions.service import SessionGatewayService
from app.core.database import get_db
from app.core.deps import current_user
from app.workflows.audit import WorkflowAuditSink

router = APIRouter(prefix="/sessions", tags=["会话网关"])

_session_gateway_service = SessionGatewayService()
_workflow_audit_sink = WorkflowAuditSink(audit_service)


def get_session_revoker() -> SessionGatewayService:
    return _session_gateway_service


def get_session_gateway_service(db: AsyncSession = Depends(get_db)) -> SessionGatewayService:
    from app.api.workflows.service import SQLAlchemyWorkflowStore, WorkflowService

    workflow_service = WorkflowService(
        store=SQLAlchemyWorkflowStore(db),
        audit_sink=_workflow_audit_sink,
        session_revoker=_session_gateway_service,
    )
    return SessionGatewayService(
        policy_client=_session_gateway_service.policy_client,
        token_store=_session_gateway_service.token_store,
        connector_scheduler=_session_gateway_service.connector_scheduler,
        session_store=_session_gateway_service.session_store,
        audit_sink=_workflow_audit_sink,
        jit_grant_client=workflow_service,
        now=_session_gateway_service.now,
        session_id_factory=_session_gateway_service.session_id_factory,
    )


def get_request_client_ip(request: Request) -> tuple[str, str]:
    if request.client is None:
        return "", "request.client"
    return request.client.host, "request.client"


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    data: SessionCreateRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    service: SessionGatewayService = Depends(get_session_gateway_service),
) -> SessionResponse:
    client_ip, client_ip_source = get_request_client_ip(request)
    try:
        session = await service.create_session(
            subject_id=str(user["id"]),
            tenant_id=str(user.get("tenant_id", "default")),
            asset_id=data.asset_id,
            account_id=data.account_id,
            protocol=data.protocol,
            connection_token=data.connection_token,
            client_ip=client_ip,
            client_ip_source=client_ip_source,
            jit_grant_id=data.jit_grant_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionResponse.from_record(session)


@router.post("/{session_id}/close", response_model=SessionResponse)
async def close_session(
    session_id: str,
    data: SessionCloseRequest,
    user: dict[str, Any] = Depends(current_user),
    service: SessionGatewayService = Depends(get_session_gateway_service),
) -> SessionResponse:
    try:
        session = await service.close_session(
            session_id=session_id,
            subject_id=str(user["id"]),
            reason=data.reason,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail == "SESSION_NOT_FOUND" else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return SessionResponse.from_record(session)
