"""Session Gateway API routes."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn, cast

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
    SqlAlchemySessionStore,
)
from app.core.config import settings
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.core.redis import create_redis_client
from app.policy.decision import PolicyDecisionService
from app.workflows.audit import WorkflowAuditSink

router = APIRouter(prefix="/sessions", tags=["会话网关"])

# 无授权 / 判定失败对外一律「资产不存在」，不暴露「没有权限」。
_ASSET_CONNECT_DENY_REASONS = frozenset(
    {
        "ASSET_PERMISSION_DENIED",
        "POLICY_EVALUATE_FAILED",
        "POLICY_CLIENT_NOT_CONFIGURED",
        "NO_MATCHING_POLICY",
        "COMMAND_POLICY_STORE_UNAVAILABLE",
    }
)


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


_OVERLAY_CONNECT_DENY_REASONS = frozenset(
    {
        "LOGIN_ASSET_ACL_REJECTED",
        "CONNECT_METHOD_ACL_REJECTED",
    }
)


def _raise_connect_denied(exc: PermissionError) -> NoReturn:
    reason = str(exc)
    if reason in _ASSET_CONNECT_DENY_REASONS:
        raise HTTPException(status_code=404, detail="资产不存在") from exc
    if reason in _OVERLAY_CONNECT_DENY_REASONS:
        raise HTTPException(status_code=403, detail="无法连接") from exc
    raise HTTPException(status_code=403, detail=reason) from exc


async def _tenant_policy_client(
    db: AsyncSession, user: dict[str, Any]
) -> PolicyDecisionServiceClient:
    """按租户装载 PolicyDecisionService（含 AssetPermission）。库失败 fail-closed。"""

    from app.policy.repository import build_tenant_policy_service
    from app.tenancy.scope import actor_scope_from_user

    try:
        service = await build_tenant_policy_service(db, actor_scope_from_user(user))
    except Exception:
        service = PolicyDecisionService(asset_permissions=[])
    return PolicyDecisionServiceClient(service)


_session_gateway_service = SessionGatewayService(
    token_store=build_connection_token_store(),
    session_store=SqlAlchemySessionStore(),
)
_workflow_audit_sink = WorkflowAuditSink(audit_service)
_fail_closed_policy_client = PolicyDecisionServiceClient(
    PolicyDecisionService(asset_permissions=[])
)


def get_session_revoker() -> SessionGatewayService:
    return _session_gateway_service


async def get_session_gateway_service(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionGatewayService:
    return _build_session_gateway_service(
        db, policy_client=await _tenant_policy_client(db, user)
    )


def get_read_session_gateway_service(
    db: AsyncSession = Depends(get_read_db),
) -> SessionGatewayService:
    return _build_session_gateway_service(db)


def _build_session_gateway_service(
    db: AsyncSession,
    *,
    policy_client: PolicyDecisionServiceClient | None = None,
) -> SessionGatewayService:
    from app.api.workflows.service import SQLAlchemyWorkflowStore, WorkflowService

    workflow_service = WorkflowService(
        store=SQLAlchemyWorkflowStore(db),
        audit_sink=_workflow_audit_sink,
        session_revoker=_session_gateway_service,
    )
    return SessionGatewayService(
        policy_client=policy_client or _fail_closed_policy_client,
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
        _raise_connect_denied(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SessionConnectionTokenResponse.from_issue(issue)


@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    user: dict[str, Any] = Depends(current_user),
    service: SessionGatewayService = Depends(get_read_session_gateway_service),
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
            subject_group_ids=tuple(str(group_id) for group_id in user.get("group_ids", ())),
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
        _raise_connect_denied(exc)
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
