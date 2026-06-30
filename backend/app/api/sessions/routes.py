"""Session Gateway API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.sessions.schemas import SessionCloseRequest, SessionCreateRequest, SessionResponse
from app.api.sessions.service import SessionGatewayService
from app.core.deps import current_user

router = APIRouter(prefix="/sessions", tags=["会话网关"])

_session_gateway_service = SessionGatewayService()


def get_session_gateway_service() -> SessionGatewayService:
    return _session_gateway_service


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
