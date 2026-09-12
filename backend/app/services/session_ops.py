"""#t78 会话共享、端点路由与存储后端。失败模式全部类型化，不回传凭据。"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import AuditKind, TypedAuditCreate
from app.models.session import SessionModel
from app.models.session_ops import (
    SessionEndpoint,
    SessionEndpointRule,
    SessionJoinRecord,
    SessionShare,
    SessionStorageBackend,
)
from app.services.audit_types import create_typed_event

SHARE_TTL = timedelta(minutes=30)
ALLOWED_STORAGE_KINDS = frozenset({"command", "replay"})
ALLOWED_STORAGE_PROVIDERS = frozenset({"local", "s3", "oss", "es"})
STORAGE_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "access_key",
        "secret_key",
        "private_key",
        "api_key",
    }
)
SAFE_HOST = re.compile(r"^[A-Za-z0-9._:-]+$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _reject_storage_secrets(config: dict[str, Any]) -> None:
    for key, value in config.items():
        lowered = key.lower()
        if lowered in STORAGE_SECRET_KEYS or any(
            part in lowered for part in ("password", "secret", "token", "access_key")
        ):
            raise ValueError("STORAGE_CONFIG_CONTAINS_SECRET")
        if isinstance(value, dict):
            _reject_storage_secrets(value)


async def _load_session(db: AsyncSession, *, tenant_id: str, session_id: str) -> SessionModel:
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id, SessionModel.tenant_id == tenant_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError("SESSION_NOT_FOUND")
    return row


async def create_share(
    db: AsyncSession,
    *,
    tenant_id: str,
    session_id: str,
    actor: dict[str, Any],
) -> tuple[SessionShare, str]:
    """创建只读共享。仅会话主体可发码；明文码只返回一次。"""

    session = await _load_session(db, tenant_id=tenant_id, session_id=session_id)
    if str(session.subject_id) != str(actor["id"]) and "admin" not in actor.get("permissions", []):
        raise ValueError("SESSION_SHARE_FORBIDDEN")
    if session.status not in {"active", "connecting"}:
        raise ValueError("SESSION_NOT_SHAREABLE")
    code = secrets.token_urlsafe(24)
    row = SessionShare(
        id=str(uuid4()),
        tenant_id=tenant_id,
        session_id=session_id,
        created_by=str(actor["id"]),
        code_hash=_code_hash(code),
        expires_at=datetime.now(UTC) + SHARE_TTL,
    )
    db.add(row)
    await db.flush()
    await create_typed_event(
        TypedAuditCreate(
            kind=AuditKind.user_session,
            action="shared",
            resource_type="session",
            resource_id=session_id,
            session_id=session_id,
            metadata={"share_id": row.id},
        ),
        actor,
    )
    return row, code


async def join_share(
    db: AsyncSession,
    *,
    tenant_id: str,
    code: str,
    actor: dict[str, Any],
) -> SessionJoinRecord:
    """用分享码以观察者身份加入。过期/吊销/跨租户一律 ``SESSION_SHARE_INVALID``。"""

    digest = _code_hash(code.strip())
    result = await db.execute(
        select(SessionShare).where(
            SessionShare.code_hash == digest, SessionShare.tenant_id == tenant_id
        )
    )
    share = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        share is None
        or share.revoked
        or share.expires_at.replace(tzinfo=share.expires_at.tzinfo or UTC) <= now
    ):
        raise ValueError("SESSION_SHARE_INVALID")
    record = SessionJoinRecord(
        id=str(uuid4()),
        tenant_id=tenant_id,
        share_id=share.id,
        session_id=share.session_id,
        joiner_id=str(actor["id"]),
        joiner_username=str(actor.get("username") or ""),
        mode="observe",
    )
    db.add(record)
    await db.flush()
    await create_typed_event(
        TypedAuditCreate(
            kind=AuditKind.user_session,
            action="joined",
            resource_type="session",
            resource_id=share.session_id,
            session_id=share.session_id,
            metadata={"share_id": share.id, "join_id": record.id, "mode": "observe"},
        ),
        actor,
    )
    return record


async def list_joins(
    db: AsyncSession, *, tenant_id: str, session_id: str
) -> list[SessionJoinRecord]:
    result = await db.execute(
        select(SessionJoinRecord)
        .where(
            SessionJoinRecord.tenant_id == tenant_id,
            SessionJoinRecord.session_id == session_id,
        )
        .order_by(SessionJoinRecord.joined_at.asc())
    )
    return list(result.scalars().all())


async def list_online_sessions(db: AsyncSession, *, tenant_id: str) -> list[SessionModel]:
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.tenant_id == tenant_id, SessionModel.status == "active")
        .order_by(SessionModel.created_at.desc())
    )
    return list(result.scalars().all())


async def create_endpoint(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    host: str,
    port: int,
    protocol: str,
) -> SessionEndpoint:
    if not SAFE_NAME.match(name) or not SAFE_HOST.match(host):
        raise ValueError("ENDPOINT_HOST_INVALID")
    if port < 1 or port > 65535:
        raise ValueError("ENDPOINT_PORT_INVALID")
    row = SessionEndpoint(
        tenant_id=tenant_id,
        name=name,
        host=host,
        port=port,
        protocol=protocol.strip().lower(),
        is_active=True,
    )
    db.add(row)
    await db.flush()
    return row


async def list_endpoints(db: AsyncSession, *, tenant_id: str) -> list[SessionEndpoint]:
    result = await db.execute(
        select(SessionEndpoint)
        .where(SessionEndpoint.tenant_id == tenant_id)
        .order_by(SessionEndpoint.id.asc())
    )
    return list(result.scalars().all())


async def create_endpoint_rule(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    priority: int,
    match_protocol: str,
    match_asset_id: str,
    endpoint_id: int,
) -> SessionEndpointRule:
    if priority < 1 or priority > 100:
        raise ValueError("ENDPOINT_RULE_PRIORITY_INVALID")
    endpoint = await db.get(SessionEndpoint, endpoint_id)
    if endpoint is None or endpoint.tenant_id != tenant_id:
        raise ValueError("ENDPOINT_NOT_FOUND")
    row = SessionEndpointRule(
        tenant_id=tenant_id,
        name=name,
        priority=priority,
        match_protocol=match_protocol.strip().lower(),
        match_asset_id=match_asset_id.strip(),
        endpoint_id=endpoint_id,
    )
    db.add(row)
    await db.flush()
    return row


async def list_endpoint_rules(db: AsyncSession, *, tenant_id: str) -> list[SessionEndpointRule]:
    result = await db.execute(
        select(SessionEndpointRule)
        .where(SessionEndpointRule.tenant_id == tenant_id)
        .order_by(SessionEndpointRule.priority.asc(), SessionEndpointRule.id.asc())
    )
    return list(result.scalars().all())


async def resolve_endpoint(
    db: AsyncSession, *, tenant_id: str, protocol: str, asset_id: str
) -> SessionEndpoint:
    """按优先级取首个协议匹配且资产为空或精确匹配的活跃端点。"""

    wanted = protocol.strip().lower()
    rules = await list_endpoint_rules(db, tenant_id=tenant_id)
    for rule in rules:
        if rule.match_protocol != wanted:
            continue
        if rule.match_asset_id and rule.match_asset_id != asset_id:
            continue
        endpoint = await db.get(SessionEndpoint, rule.endpoint_id)
        if endpoint is None or endpoint.tenant_id != tenant_id or not endpoint.is_active:
            continue
        return endpoint
    raise ValueError("ENDPOINT_UNRESOLVED")


async def create_storage_backend(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    kind: str,
    provider: str,
    config: dict[str, Any],
) -> SessionStorageBackend:
    if kind not in ALLOWED_STORAGE_KINDS:
        raise ValueError("STORAGE_KIND_UNSUPPORTED")
    if provider not in ALLOWED_STORAGE_PROVIDERS:
        raise ValueError("STORAGE_PROVIDER_UNSUPPORTED")
    if not SAFE_NAME.match(name):
        raise ValueError("STORAGE_NAME_INVALID")
    _reject_storage_secrets(config)
    row = SessionStorageBackend(
        tenant_id=tenant_id,
        name=name,
        kind=kind,
        provider=provider,
        config=config,
    )
    db.add(row)
    await db.flush()
    return row


async def list_storage_backends(
    db: AsyncSession, *, tenant_id: str, kind: str | None = None
) -> list[SessionStorageBackend]:
    statement = select(SessionStorageBackend).where(SessionStorageBackend.tenant_id == tenant_id)
    if kind:
        statement = statement.where(SessionStorageBackend.kind == kind)
    result = await db.execute(statement.order_by(SessionStorageBackend.id.asc()))
    return list(result.scalars().all())


def local_storage_root(tenant_id: str, backend: SessionStorageBackend) -> Path:
    """本地/镜像前缀目录。s3/oss/es 本切片只落本地适配前缀，不调云 SDK。"""

    prefix = str(backend.config.get("prefix") or backend.name)
    safe_prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", prefix)
    root = (
        Path(tempfile.gettempdir())
        / "janusgate-session-storage"
        / tenant_id
        / backend.kind
        / backend.provider
        / safe_prefix
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_command_document(
    *,
    tenant_id: str,
    backend: SessionStorageBackend,
    recording_id: str,
    command: str,
    sequence: int,
) -> Path:
    """把一条命令写入本地适配存储，供 ES/本地命令检索。"""

    if backend.kind != "command":
        raise ValueError("STORAGE_KIND_UNSUPPORTED")
    root = local_storage_root(tenant_id, backend)
    path = root / f"{recording_id}-{sequence}.json"
    path.write_text(
        json.dumps(
            {
                "recording_id": recording_id,
                "sequence": sequence,
                "command": command,
                "provider": backend.provider,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def search_commands(*, tenant_id: str, backend: SessionStorageBackend, query: str) -> list[dict[str, Any]]:
    """在本地适配前缀中按子串检索命令。云上 ES 查询留给 #t70。"""

    root = local_storage_root(tenant_id, backend)
    needle = query.strip().lower()
    hits: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        command = str(payload.get("command") or "")
        if needle and needle not in command.lower():
            continue
        hits.append(payload)
    return hits
