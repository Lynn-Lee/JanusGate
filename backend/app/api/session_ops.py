"""#t78 会话共享、端点路由与存储后端 API。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.services import session_ops as ops

router = APIRouter(prefix="/session-ops", tags=["会话高级能力"])


class SessionShareCreateResponse(BaseModel):
    id: str
    session_id: str
    code: str
    expires_at: datetime
    mode: str = "observe"


class SessionJoinRequest(BaseModel):
    code: str = Field(min_length=8, max_length=128)


class SessionJoinResponse(BaseModel):
    id: str
    session_id: str
    share_id: str
    joiner_username: str
    mode: str


class SessionJoinListResponse(BaseModel):
    items: list[SessionJoinResponse]
    total: int


class EndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    protocol: str = Field(default="", max_length=32)


class EndpointResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    protocol: str
    is_active: bool


class EndpointListResponse(BaseModel):
    items: list[EndpointResponse]
    total: int


class EndpointRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    priority: int = Field(ge=1, le=100, default=10)
    match_protocol: str = Field(min_length=1, max_length=32)
    match_asset_id: str = Field(default="", max_length=120)
    endpoint_id: int = Field(gt=0)


class EndpointRuleResponse(BaseModel):
    id: int
    name: str
    priority: int
    match_protocol: str
    match_asset_id: str
    endpoint_id: int


class EndpointRuleListResponse(BaseModel):
    items: list[EndpointRuleResponse]
    total: int


class EndpointResolveRequest(BaseModel):
    protocol: str = Field(min_length=1, max_length=32)
    asset_id: str = Field(min_length=1, max_length=120)


class StorageBackendCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=16)
    provider: str = Field(min_length=1, max_length=16)
    config: dict[str, Any] = Field(default_factory=dict)


class StorageBackendResponse(BaseModel):
    id: int
    name: str
    kind: str
    provider: str
    config: dict[str, Any]


class StorageBackendListResponse(BaseModel):
    items: list[StorageBackendResponse]
    total: int


class CommandSearchResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int


def _require(user: dict[str, Any], *permissions: str) -> None:
    granted = set(user.get("permissions", []))
    if "admin" in granted or granted.intersection(permissions):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限: {permissions[0]}")


def _http(exc: ValueError) -> HTTPException:
    detail = str(exc)
    if detail == "SESSION_NOT_FOUND":
        return HTTPException(status_code=404, detail=detail)
    if detail == "SESSION_SHARE_FORBIDDEN":
        return HTTPException(status_code=403, detail=detail)
    if detail == "STORAGE_BACKEND_NOT_FOUND":
        return HTTPException(status_code=404, detail=detail)
    return HTTPException(status_code=400, detail=detail)


@router.post(
    "/sessions/{session_id}/shares",
    response_model=SessionShareCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_share(
    session_id: str,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionShareCreateResponse:
    _require(user, "sessions:connect")
    try:
        row, code = await ops.create_share(
            db,
            tenant_id=str(user["tenant_id"]),
            session_id=session_id,
            actor=user,
        )
        await db.commit()
    except ValueError as exc:
        raise _http(exc) from exc
    return SessionShareCreateResponse(
        id=row.id, session_id=row.session_id, code=code, expires_at=row.expires_at
    )


@router.post("/joins", response_model=SessionJoinResponse, status_code=status.HTTP_201_CREATED)
async def join_session_share(
    payload: SessionJoinRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionJoinResponse:
    _require(user, "sessions:connect")
    try:
        row = await ops.join_share(
            db, tenant_id=str(user["tenant_id"]), code=payload.code, actor=user
        )
        await db.commit()
    except ValueError as exc:
        raise _http(exc) from exc
    return SessionJoinResponse(
        id=row.id,
        session_id=row.session_id,
        share_id=row.share_id,
        joiner_username=row.joiner_username,
        mode=row.mode,
    )


@router.get("/sessions/{session_id}/joins", response_model=SessionJoinListResponse)
async def list_session_joins(
    session_id: str,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> SessionJoinListResponse:
    _require(user, "sessions:connect", "audit:read")
    rows = await ops.list_joins(db, tenant_id=str(user["tenant_id"]), session_id=session_id)
    items = [
        SessionJoinResponse(
            id=row.id,
            session_id=row.session_id,
            share_id=row.share_id,
            joiner_username=row.joiner_username,
            mode=row.mode,
        )
        for row in rows
    ]
    return SessionJoinListResponse(items=items, total=len(items))


@router.get("/endpoints", response_model=EndpointListResponse)
async def get_endpoints(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> EndpointListResponse:
    _require(user, "admin", "sessions:connect")
    rows = await ops.list_endpoints(db, tenant_id=str(user["tenant_id"]))
    items = [
        EndpointResponse(
            id=row.id,
            name=row.name,
            host=row.host,
            port=row.port,
            protocol=row.protocol,
            is_active=row.is_active,
        )
        for row in rows
    ]
    return EndpointListResponse(items=items, total=len(items))


@router.post("/endpoints", response_model=EndpointResponse, status_code=status.HTTP_201_CREATED)
async def post_endpoint(
    payload: EndpointCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> EndpointResponse:
    _require(user, "admin")
    try:
        row = await ops.create_endpoint(
            db,
            tenant_id=str(user["tenant_id"]),
            name=payload.name,
            host=payload.host,
            port=payload.port,
            protocol=payload.protocol,
        )
        await db.commit()
        await db.refresh(row)
    except ValueError as exc:
        raise _http(exc) from exc
    return EndpointResponse(
        id=row.id,
        name=row.name,
        host=row.host,
        port=row.port,
        protocol=row.protocol,
        is_active=row.is_active,
    )


@router.get("/endpoint-rules", response_model=EndpointRuleListResponse)
async def get_endpoint_rules(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> EndpointRuleListResponse:
    _require(user, "admin", "sessions:connect")
    rows = await ops.list_endpoint_rules(db, tenant_id=str(user["tenant_id"]))
    items = [
        EndpointRuleResponse(
            id=row.id,
            name=row.name,
            priority=row.priority,
            match_protocol=row.match_protocol,
            match_asset_id=row.match_asset_id,
            endpoint_id=row.endpoint_id,
        )
        for row in rows
    ]
    return EndpointRuleListResponse(items=items, total=len(items))


@router.post(
    "/endpoint-rules",
    response_model=EndpointRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_endpoint_rule(
    payload: EndpointRuleCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> EndpointRuleResponse:
    _require(user, "admin")
    try:
        row = await ops.create_endpoint_rule(
            db,
            tenant_id=str(user["tenant_id"]),
            name=payload.name,
            priority=payload.priority,
            match_protocol=payload.match_protocol,
            match_asset_id=payload.match_asset_id,
            endpoint_id=payload.endpoint_id,
        )
        await db.commit()
        await db.refresh(row)
    except ValueError as exc:
        raise _http(exc) from exc
    return EndpointRuleResponse(
        id=row.id,
        name=row.name,
        priority=row.priority,
        match_protocol=row.match_protocol,
        match_asset_id=row.match_asset_id,
        endpoint_id=row.endpoint_id,
    )


@router.post("/endpoints/resolve", response_model=EndpointResponse)
async def resolve_session_endpoint(
    payload: EndpointResolveRequest,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> EndpointResponse:
    _require(user, "sessions:connect", "admin")
    try:
        row = await ops.resolve_endpoint(
            db,
            tenant_id=str(user["tenant_id"]),
            protocol=payload.protocol,
            asset_id=payload.asset_id,
        )
    except ValueError as exc:
        raise _http(exc) from exc
    return EndpointResponse(
        id=row.id,
        name=row.name,
        host=row.host,
        port=row.port,
        protocol=row.protocol,
        is_active=row.is_active,
    )


@router.get("/storage-backends", response_model=StorageBackendListResponse)
async def get_storage_backends(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
    kind: str | None = Query(default=None, max_length=16),
) -> StorageBackendListResponse:
    _require(user, "admin", "audit:read")
    rows = await ops.list_storage_backends(db, tenant_id=str(user["tenant_id"]), kind=kind)
    items = [
        StorageBackendResponse(
            id=row.id, name=row.name, kind=row.kind, provider=row.provider, config=row.config
        )
        for row in rows
    ]
    return StorageBackendListResponse(items=items, total=len(items))


@router.post(
    "/storage-backends",
    response_model=StorageBackendResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_storage_backend(
    payload: StorageBackendCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> StorageBackendResponse:
    _require(user, "admin")
    try:
        row = await ops.create_storage_backend(
            db,
            tenant_id=str(user["tenant_id"]),
            name=payload.name,
            kind=payload.kind,
            provider=payload.provider,
            config=payload.config,
        )
        await db.commit()
        await db.refresh(row)
    except ValueError as exc:
        raise _http(exc) from exc
    return StorageBackendResponse(
        id=row.id, name=row.name, kind=row.kind, provider=row.provider, config=row.config
    )


@router.get(
    "/storage-backends/{backend_id}/commands",
    response_model=CommandSearchResponse,
)
async def search_storage_commands(
    backend_id: int,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
    q: str = Query(default="", max_length=200),
) -> CommandSearchResponse:
    """在已登记的 command 存储（含 ES 本地适配前缀）中检索命令。"""

    _require(user, "audit:read", "admin")
    rows = await ops.list_storage_backends(db, tenant_id=str(user["tenant_id"]), kind="command")
    backend = next((row for row in rows if row.id == backend_id), None)
    if backend is None:
        raise HTTPException(status_code=404, detail="STORAGE_BACKEND_NOT_FOUND")
    hits = ops.search_commands(tenant_id=str(user["tenant_id"]), backend=backend, query=q)
    return CommandSearchResponse(items=hits, total=len(hits))
