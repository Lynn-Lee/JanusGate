"""Session Gateway API routes."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import AuditCategory, AuditEventCreate, AuditSeverity
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
from app.models.session import SessionModel
from app.models.session_ops import SessionShare
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
        "HOST_KEY_UNAPPROVED",
        "HOST_KEY_REJECTED",
        "HOST_KEY_CHANGED",
        "SSH_TRUSTED_HOST_KEY_MISSING",
        "SSH_HOST_KEY_REJECTED",
        "CONNECTOR_PROTOCOL_UNSUPPORTED",
        "CONNECTOR_TARGET_UNRESOLVED",
        "ZONE_GATEWAY_UNAVAILABLE",
        "K8S_NAMESPACE_FORBIDDEN",
        "K8S_NAMESPACE_OVERREACH",
        "K8S_NAMESPACE_MISSING",
        "K8S_TARGET_INCOMPLETE",
        "K8S_POD_REQUIRED",
        "K8S_TLS_CA_INVALID",
        "K8S_CREDENTIAL_MISSING",
        "K8S_COMMAND_DENIED",
        "K8S_TLS_HANDSHAKE_FAILED",
        "K8S_EXEC_REJECTED",
        "K8S_CONNECT_TIMEOUT",
        "K8S_CONNECT_FAILED",
    }
)

_K8S_HTTPS_CA_DENY_REASONS = frozenset(
    {
        "K8S_INSECURE_TRANSPORT",
        "K8S_TLS_CA_MISSING",
        "K8S_HTTPS_CA_REQUIRED",
    }
)
K8S_HTTPS_CA_DENIED_COPY = "无法连接（需要 HTTPS 和 CA）"


def _raise_connect_denied(exc: BaseException) -> NoReturn:
    reason = getattr(exc, "code", None) or str(exc)
    if reason in _ASSET_CONNECT_DENY_REASONS:
        raise HTTPException(status_code=404, detail="资产不存在") from exc
    if reason in _K8S_HTTPS_CA_DENY_REASONS:
        raise HTTPException(status_code=403, detail=K8S_HTTPS_CA_DENIED_COPY) from exc
    if reason in _OVERLAY_CONNECT_DENY_REASONS:
        raise HTTPException(status_code=403, detail="无法连接") from exc
    if "没有权限" in str(reason) or "越权" in str(reason):
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


def _production_connector_scheduler():
    from app.connectors.session_runtime import build_production_connector_scheduler

    return build_production_connector_scheduler()


_session_gateway_service = SessionGatewayService(
    token_store=build_connection_token_store(),
    session_store=SqlAlchemySessionStore(),
    connector_scheduler=_production_connector_scheduler(),
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
            pod=data.pod,
            container=data.container,
        )
    except PermissionError as exc:
        _raise_connect_denied(exc)
    except Exception as exc:
        code = getattr(exc, "code", "")
        if (
            code in _OVERLAY_CONNECT_DENY_REASONS
            or code in _K8S_HTTPS_CA_DENY_REASONS
            or str(exc) in _OVERLAY_CONNECT_DENY_REASONS
            or str(exc) in _K8S_HTTPS_CA_DENY_REASONS
        ):
            _raise_connect_denied(exc)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
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


class SessionShareCreate(BaseModel):
    guest_user_id: str = Field(min_length=1, max_length=64)
    mode: str = Field(pattern="^(watch|join)$")


@router.get("/{session_id}/shares")
async def list_session_shares(
    session_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SessionShare).where(
            SessionShare.tenant_id == tenant_id,
            SessionShare.session_id == session_id,
        )
    )
    items = [
        {
            "id": share.id,
            "guest_user_id": share.guest_user_id,
            "mode": share.mode,
            "status": share.status,
            "joined_at": share.joined_at,
        }
        for share in result.scalars().all()
        if share.owner_user_id == str(user["id"]) or share.guest_user_id == str(user["id"])
    ]
    return {"items": items, "total": len(items)}


@router.post("/{session_id}/shares", status_code=status.HTTP_201_CREATED)
async def create_session_share(
    session_id: str,
    data: SessionShareCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    session = await db.get(SessionModel, session_id)
    tenant_id = str(user.get("tenant_id") or "default")
    if session is None or session.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
    if session.subject_id != str(user["id"]):
        raise HTTPException(status_code=403, detail="SESSION_SHARE_OWNER_ONLY")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="SESSION_NOT_ACTIVE")
    share = SessionShare(
        id=f"ss_{uuid4().hex}",
        tenant_id=tenant_id,
        session_id=session_id,
        owner_user_id=str(user["id"]),
        guest_user_id=data.guest_user_id,
        mode=data.mode,
        status="pending",
    )
    db.add(share)
    await db.commit()
    await audit_service.create_event(
        AuditEventCreate(
            event_type="session.share",
            category=AuditCategory.session,
            action="share",
            resource_type="session",
            resource_id=session_id,
            session_id=session_id,
            severity=AuditSeverity.medium,
            message="session shared",
            metadata={"guest_user_id": data.guest_user_id, "mode": data.mode},
        ),
        user,
    )
    return {"id": share.id, "status": share.status, "mode": share.mode}


@router.post("/{session_id}/shares/{share_id}/join")
async def join_session_share(
    session_id: str,
    share_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    share = await db.get(SessionShare, share_id)
    tenant_id = str(user.get("tenant_id") or "default")
    if share is None or share.tenant_id != tenant_id or share.session_id != session_id:
        raise HTTPException(status_code=404, detail="SESSION_SHARE_NOT_FOUND")
    if share.guest_user_id != str(user["id"]):
        raise HTTPException(status_code=403, detail="SESSION_JOIN_FORBIDDEN")
    share.status = "joined"
    share.joined_at = datetime.now(UTC)
    await db.commit()
    await audit_service.create_event(
        AuditEventCreate(
            event_type="session.join",
            category=AuditCategory.session,
            action="join",
            resource_type="session",
            resource_id=session_id,
            session_id=session_id,
            severity=AuditSeverity.medium,
            message="session joined",
            metadata={"share_id": share_id, "mode": share.mode},
        ),
        user,
    )
    return {"id": share.id, "status": share.status, "mode": share.mode}
