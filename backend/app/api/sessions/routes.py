"""Session Gateway API routes."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.service import audit_service
from app.api.sessions.schemas import (
    SessionCloseRequest,
    SessionConnectionTokenRequest,
    SessionConnectionTokenResponse,
    SessionCreateRequest,
    SessionListResponse,
    SessionResponse,
)
from app.api.sessions.service import (
    ConnectionTokenStore,
    InMemoryConnectionTokenStore,
    PolicyDecisionServiceClient,
    RedisConnectionTokenClient,
    RedisConnectionTokenStore,
    SessionGatewayService,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import current_user
from app.core.redis import create_redis_client
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import PolicyRule
from app.workflows.audit import WorkflowAuditSink

router = APIRouter(prefix="/sessions", tags=["会话网关"])


def build_connection_token_store(
    *,
    store: str = settings.SESSION_CONNECTION_TOKEN_STORE,
    redis_url: str = settings.REDIS_URL,
    redis_key_prefix: str = settings.SESSION_CONNECTION_TOKEN_REDIS_KEY_PREFIX,
    redis_factory: Callable[[str], RedisConnectionTokenClient] | None = None,
) -> ConnectionTokenStore:
    if store == "memory":
        return InMemoryConnectionTokenStore()
    if store == "redis":
        redis_settings = settings.model_copy(update={"REDIS_URL": redis_url})
        factory = redis_factory or (
            lambda _url: cast(RedisConnectionTokenClient, create_redis_client(settings=redis_settings))
        )
        return RedisConnectionTokenStore(factory(redis_url), key_prefix=redis_key_prefix)
    raise ValueError("UNSUPPORTED_SESSION_CONNECTION_TOKEN_STORE")


_session_gateway_service = SessionGatewayService(token_store=build_connection_token_store())
_workflow_audit_sink = WorkflowAuditSink(audit_service)
_session_policy_client = PolicyDecisionServiceClient(
    PolicyDecisionService(
        rules=[
            PolicyRule(
                id="approved-jit-session",
                subject_ids=["*"],
                actions=["session.connect"],
                resource_ids=["*"],
                tenant_id="*",
                require_approval=True,
            )
        ]
    )
)


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
        policy_client=_session_policy_client,
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


@router.post(
    "/connection-token",
    response_model=SessionConnectionTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_connection_token(
    data: SessionConnectionTokenRequest,
    user: dict[str, Any] = Depends(current_user),
    service: SessionGatewayService = Depends(get_session_gateway_service),
) -> SessionConnectionTokenResponse:
    try:
        issue = await service.issue_connection_token(
            subject_id=str(user["id"]),
            tenant_id=str(user.get("tenant_id", "default")),
            asset_id=data.asset_id,
            account_id=data.account_id,
            protocol=data.protocol,
            action=data.action,
            jit_grant_id=data.jit_grant_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionConnectionTokenResponse.from_issue(issue)


@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    user: dict[str, Any] = Depends(current_user),
    service: SessionGatewayService = Depends(get_session_gateway_service),
) -> SessionListResponse:
    sessions = await service.list_sessions(
        subject_id=str(user["id"]),
        tenant_id=str(user.get("tenant_id", "default")),
    )
    return SessionListResponse.from_records(sessions)


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
