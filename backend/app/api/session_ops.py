"""#t78 连接端点路由与命令/录像存储后端 API。"""
from __future__ import annotations

import base64
import json
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.session_ops import ConnectionEndpoint, EndpointRule, SessionStorageBackend
from app.services.endpoint_routing import resolve_endpoint
from app.services.session_storage import (
    SessionObjectStore,
    dumps_command_event,
    public_storage_config,
    validate_storage_config,
)

router = APIRouter(prefix="/session-ops", tags=["会话高级能力"])


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=256)
    port: int = Field(ge=1, le=65535)
    protocol: str = Field(min_length=1, max_length=32)


class EndpointRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    match_protocol: str = Field(min_length=1, max_length=32)
    match_host_suffix: str = Field(default="", max_length=256)
    endpoint_id: str = Field(min_length=1, max_length=64)
    priority: int = 0


class StorageBackendCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=2, max_length=16)
    purpose: str = Field(min_length=3, max_length=16)
    config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class StorageObjectPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1, max_length=120)
    command: dict[str, Any] | None = None
    replay_b64: str | None = None


def _require_admin(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or "sessions:connect" in permissions:
        return
    raise HTTPException(status_code=403, detail="缺少权限: sessions:connect")


@router.get("/endpoints")
async def list_endpoints(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(ConnectionEndpoint).where(ConnectionEndpoint.tenant_id == tenant_id)
    )
    items = [
        {
            "id": item.id,
            "name": item.name,
            "host": item.host,
            "port": item.port,
            "protocol": item.protocol,
            "enabled": item.enabled,
        }
        for item in result.scalars().all()
    ]
    return {"items": items, "total": len(items)}


@router.post("/endpoints", status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    data: EndpointCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    endpoint = ConnectionEndpoint(
        id=f"ep_{uuid4().hex}",
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        host=data.host,
        port=data.port,
        protocol=data.protocol,
        enabled=True,
        created_by=str(user.get("id") or ""),
    )
    db.add(endpoint)
    await db.commit()
    return {"id": endpoint.id, "name": endpoint.name, "host": endpoint.host, "port": endpoint.port, "protocol": endpoint.protocol}


@router.post("/endpoint-rules", status_code=status.HTTP_201_CREATED)
async def create_endpoint_rule(
    data: EndpointRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = str(user.get("tenant_id") or "default")
    endpoint = await db.get(ConnectionEndpoint, data.endpoint_id)
    if endpoint is None or endpoint.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="ENDPOINT_NOT_FOUND")
    rule = EndpointRule(
        id=f"er_{uuid4().hex}",
        tenant_id=tenant_id,
        name=data.name,
        match_protocol=data.match_protocol,
        match_host_suffix=data.match_host_suffix,
        endpoint_id=endpoint.id,
        priority=data.priority,
        enabled=True,
    )
    db.add(rule)
    await db.commit()
    return {"id": rule.id, "endpoint_id": rule.endpoint_id, "priority": rule.priority}


@router.get("/endpoint-rules/resolve")
async def resolve_endpoint_rule(
    protocol: Annotated[str, Query(min_length=1, max_length=32)],
    host: Annotated[str, Query(min_length=1, max_length=256)],
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    endpoint = await resolve_endpoint(
        db,
        tenant_id=str(user.get("tenant_id") or "default"),
        protocol=protocol,
        host=host,
    )
    if endpoint is None:
        raise HTTPException(status_code=404, detail="ENDPOINT_NOT_FOUND")
    return {"id": endpoint.id, "host": endpoint.host, "port": endpoint.port, "protocol": endpoint.protocol}


@router.get("/storage-backends")
async def list_storage_backends(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SessionStorageBackend).where(SessionStorageBackend.tenant_id == tenant_id)
    )
    items = []
    for backend in result.scalars().all():
        config = json.loads(backend.config_json or "{}")
        items.append(
            {
                "id": backend.id,
                "name": backend.name,
                "kind": backend.kind,
                "purpose": backend.purpose,
                "is_default": backend.is_default,
                "config": public_storage_config(config),
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/storage-backends", status_code=status.HTTP_201_CREATED)
async def create_storage_backend(
    data: StorageBackendCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    try:
        config = validate_storage_config(kind=data.kind, purpose=data.purpose, config=data.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    backend = SessionStorageBackend(
        id=f"sb_{uuid4().hex}",
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        kind=data.kind,
        purpose=data.purpose,
        config_json=json.dumps(config, sort_keys=True),
        is_default=data.is_default,
        created_by=str(user.get("id") or ""),
    )
    db.add(backend)
    await db.commit()
    return {"id": backend.id, "kind": backend.kind, "purpose": backend.purpose, "config": public_storage_config(config)}


@router.post("/storage-backends/{backend_id}/objects", status_code=status.HTTP_201_CREATED)
async def put_storage_object(
    backend_id: str,
    data: StorageObjectPut,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, str]:
    _require_admin(user)
    backend = await db.get(SessionStorageBackend, backend_id)
    tenant_id = str(user.get("tenant_id") or "default")
    if backend is None or backend.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="STORAGE_BACKEND_NOT_FOUND")
    config = json.loads(backend.config_json or "{}")
    store = SessionObjectStore()
    if backend.purpose == "command":
        if not data.command:
            raise HTTPException(status_code=400, detail="COMMAND_PAYLOAD_REQUIRED")
        uri = store.put(
            tenant_id=tenant_id,
            kind=backend.kind,
            purpose="command",
            object_id=data.object_id,
            payload=dumps_command_event(data.command),
            config=config,
        )
    else:
        if not data.replay_b64:
            raise HTTPException(status_code=400, detail="REPLAY_PAYLOAD_REQUIRED")
        uri = store.put(
            tenant_id=tenant_id,
            kind=backend.kind,
            purpose="replay",
            object_id=data.object_id,
            payload=base64.b64decode(data.replay_b64),
            config=config,
        )
    return {"uri": uri}


@router.get("/storage-backends/{backend_id}/commands")
async def search_stored_commands(
    backend_id: str,
    q: Annotated[str, Query(min_length=1, max_length=120)],
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_admin(user)
    backend = await db.get(SessionStorageBackend, backend_id)
    tenant_id = str(user.get("tenant_id") or "default")
    if backend is None or backend.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="STORAGE_BACKEND_NOT_FOUND")
    config = json.loads(backend.config_json or "{}")
    hits = SessionObjectStore().search_commands(
        tenant_id=tenant_id, kind=backend.kind, query=q, config=config
    )
    return {"items": hits, "total": len(hits)}
