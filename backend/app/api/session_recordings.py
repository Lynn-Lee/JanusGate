"""Phase 4 session recording and command search API routes."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.api.audits.schemas import AuditCategory, AuditEventCreate, AuditSeverity
from app.api.audits.service import audit_service
from app.api.session_recording_schemas import (
    FileTransferLogCreate,
    FileTransferLogListResponse,
    FileTransferLogResponse,
    SessionCommandEventCreate,
    SessionCommandEventListResponse,
    SessionCommandEventResponse,
    SessionRecordingCreate,
    SessionRecordingResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.connector import Connector
from app.models.file_transfer import FileTransferLog
from app.models.session_recording import SessionCommandEvent, SessionRecording
from app.policy.repository import build_tenant_policy_service
from app.policy.schemas import (
    CommandDecisionRequest,
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingRequest,
    ResourceRef,
    SubjectRef,
)
from app.tenancy.scope import actor_scope_from_user

router = APIRouter(tags=["会话录制"])

_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|credential)=\S+"
)


@router.post(
    "/sessions/{session_id}/recordings",
    response_model=SessionRecordingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_recording(
    session_id: str,
    data: SessionRecordingCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionRecordingResponse:
    _require_recording_permission(user, "session-recordings:write")
    recording = SessionRecording(
        tenant_id=str(user.get("tenant_id") or "default"),
        session_id=session_id,
        subject_id=str(user.get("id") or ""),
        asset_id=data.asset_id,
        account_id=data.account_id,
        protocol=data.protocol,
        storage_uri=data.storage_uri,
        status="recording",
    )
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return _recording_response(recording)


@router.post(
    "/session-recordings/{recording_id}/commands",
    response_model=SessionCommandEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_session_command_event(
    recording_id: int,
    data: SessionCommandEventCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionCommandEventResponse:
    _require_recording_permission(user, "session-recordings:write")
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    _ensure_recording_is_open(recording)
    event = await _persist_command_event(db=db, user=user, recording=recording, data=data)
    return _command_response(event)


@router.post(
    "/connectors/{connector_id}/session-recordings/{recording_id}/commands",
    response_model=SessionCommandEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_connector_session_command_event(
    connector_id: int,
    recording_id: int,
    data: SessionCommandEventCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionCommandEventResponse:
    _require_recording_permission(user, "connectors:write")
    await _get_active_scoped_connector(db=db, user=user, connector_id=connector_id)
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    _ensure_recording_is_open(recording)
    event = await _persist_command_event(db=db, user=user, recording=recording, data=data)
    return _command_response(event)


@router.get(
    "/session-recordings/{recording_id}/commands",
    response_model=SessionCommandEventListResponse,
)
async def list_session_recording_commands(
    recording_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionCommandEventListResponse:
    _require_recording_permission(user, "session-recordings:read")
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    result = await db.execute(
        select(SessionCommandEvent)
        .where(SessionCommandEvent.tenant_id == recording.tenant_id)
        .where(SessionCommandEvent.recording_id == recording.id)
        .order_by(SessionCommandEvent.sequence.asc(), SessionCommandEvent.id.asc())
    )
    events = result.scalars().all()
    items = [_command_response(event) for event in events]
    return SessionCommandEventListResponse(items=items, total=len(items))


@router.post(
    "/session-recordings/{recording_id}/close",
    response_model=SessionRecordingResponse,
)
async def close_session_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionRecordingResponse:
    _require_recording_permission(user, "session-recordings:write")
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    if recording.status != "recording":
        raise HTTPException(status_code=404, detail="SESSION_RECORDING_NOT_FOUND")

    recording.status = "closed"
    recording.ended_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(recording)
    return _recording_response(recording)


@router.post(
    "/session-recordings/{recording_id}/file-transfers",
    response_model=FileTransferLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_session_file_transfer(
    recording_id: int,
    data: FileTransferLogCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> FileTransferLogResponse:
    """把一次 SFTP 传输写入分类日志，并先并入 #t61 hash chain。"""

    _require_recording_permission(user, "session-recordings:write")
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    _ensure_recording_is_open(recording)
    log = await _persist_file_transfer(db=db, user=user, recording=recording, data=data)
    return _file_transfer_response(log)


@router.post(
    "/connectors/{connector_id}/session-recordings/{recording_id}/file-transfers",
    response_model=FileTransferLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_connector_session_file_transfer(
    connector_id: int,
    recording_id: int,
    data: FileTransferLogCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> FileTransferLogResponse:
    """连接器上报文件传输事件；跨租户或 inactive connector fail-closed。"""

    _require_recording_permission(user, "connectors:write")
    await _get_active_scoped_connector(db=db, user=user, connector_id=connector_id)
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    _ensure_recording_is_open(recording)
    log = await _persist_file_transfer(db=db, user=user, recording=recording, data=data)
    return _file_transfer_response(log)


@router.get(
    "/session-recordings/{recording_id}/file-transfers",
    response_model=FileTransferLogListResponse,
)
async def list_session_recording_file_transfers(
    recording_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> FileTransferLogListResponse:
    _require_any_recording_read_permission(user)
    recording = await _get_scoped_recording(db=db, user=user, recording_id=recording_id)
    result = await db.execute(
        select(FileTransferLog)
        .where(FileTransferLog.tenant_id == recording.tenant_id)
        .where(FileTransferLog.recording_id == recording.id)
        .order_by(FileTransferLog.occurred_at.asc(), FileTransferLog.id.asc())
    )
    logs = result.scalars().all()
    items = [_file_transfer_response(log) for log in logs]
    return FileTransferLogListResponse(items=items, total=len(items))


@router.get("/file-transfers/", response_model=FileTransferLogListResponse)
async def list_file_transfers(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> FileTransferLogListResponse:
    """当前租户文件传输日志列表（不返回文件正文）。"""

    _require_any_recording_read_permission(user)
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(FileTransferLog)
        .where(FileTransferLog.tenant_id == tenant_id)
        .order_by(FileTransferLog.occurred_at.desc(), FileTransferLog.id.desc())
    )
    logs = result.scalars().all()
    items = [_file_transfer_response(log) for log in logs]
    return FileTransferLogListResponse(items=items, total=len(items))


@router.get("/session-recordings/commands", response_model=SessionCommandEventListResponse)
async def search_session_commands(
    query: str = Query(min_length=1, max_length=120),
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionCommandEventListResponse:
    _require_recording_permission(user, "session-recordings:read")
    tenant_id = str(user.get("tenant_id") or "default")
    dialect_name = db.get_bind().dialect.name
    result = await db.execute(
        select(SessionCommandEvent)
        .where(SessionCommandEvent.tenant_id == tenant_id)
        .where(_build_command_search_filter(query=query, dialect_name=dialect_name))
        .order_by(SessionCommandEvent.occurred_at.desc(), SessionCommandEvent.id.desc())
    )
    events = result.scalars().all()
    items = [_command_response(event) for event in events]
    return SessionCommandEventListResponse(items=items, total=len(items))


async def _persist_command_event(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    recording: SessionRecording,
    data: SessionCommandEventCreate,
) -> SessionCommandEvent:
    """在入库前走 PolicyDecisionService：拒绝则阻断并审计，放行则脱敏后落库。

    SSH / K8s 连接器均经 ``HttpCommandEventSink`` POST 到本路径，因此这是 #t65
    命令过滤与脱敏在生产管线上的接线点。拒绝时**不落明文命令**。
    """

    policy = await build_tenant_policy_service(db, actor_scope_from_user(user))
    tenant_id = recording.tenant_id
    subject = SubjectRef(
        id=recording.subject_id or str(user.get("id") or ""),
        type="user",
        tenant_id=tenant_id,
    )
    resource = ResourceRef(
        id=recording.asset_id,
        type=recording.protocol or "asset",
        tenant_id=tenant_id,
    )
    try:
        decision = policy.evaluate_command(
            CommandDecisionRequest(
                subject=subject,
                resource=resource,
                account_id=recording.account_id,
                command=data.command,
            )
        )
    except Exception:
        decision = CommandDecisionResponse(
            effect=CommandFilterEffect.DENY,
            action="reject",
            reason_code="COMMAND_EVALUATE_FAILED",
            explain_trace=["evaluate_command_failed"],
            audit_event_id=f"pde_{uuid4().hex}",
        )
    if decision.effect in (CommandFilterEffect.DENY, CommandFilterEffect.REVIEW):
        await _audit_rejected_command(
            user=user, recording=recording, data=data, decision=decision
        )
        raise HTTPException(status_code=403, detail=decision.reason_code)

    masking = policy.mask(
        MaskingRequest(
            subject=subject,
            resource=resource,
            account_id=recording.account_id,
            text=data.output_excerpt,
        )
    )
    event = SessionCommandEvent(
        tenant_id=recording.tenant_id,
        recording_id=recording.id,
        session_id=recording.session_id,
        sequence=data.sequence,
        command=data.command,
        exit_code=data.exit_code,
        output_excerpt=_redact_command_excerpt(masking.masked_text),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def _persist_file_transfer(
    *,
    db: AsyncSession,
    user: dict[str, Any],
    recording: SessionRecording,
    data: FileTransferLogCreate,
) -> FileTransferLog:
    """先写 hash chain，再落分类日志；失败传输同样入库，不保存文件正文。

    路径中的 ``token=/password=/secret=/credential=`` 赋值片段会脱敏。审计 metadata
    只保留路径、方向、字节数、摘要和状态，不落文件内容。
    """

    digest = _validate_file_transfer_sha256(data)
    remote_path = _redact_command_excerpt(data.remote_path)
    severity = (
        AuditSeverity.medium if data.status == "failed" else AuditSeverity.low
    )
    audit = await audit_service.create_event(
        AuditEventCreate(
            event_type="session.file_transfer",
            category=AuditCategory.session,
            action=f"file.{data.direction}",
            resource_type="session_recording",
            resource_id=str(recording.id),
            session_id=recording.session_id,
            severity=severity,
            message="File transfer recorded",
            metadata={
                "remote_path": remote_path,
                "direction": data.direction,
                "size_bytes": data.size_bytes,
                "sha256": digest,
                "status": data.status,
                "error_code": data.error_code,
            },
        ),
        user,
    )
    log = FileTransferLog(
        tenant_id=recording.tenant_id,
        recording_id=recording.id,
        session_id=recording.session_id,
        asset_id=recording.asset_id,
        account_id=recording.account_id,
        remote_path=remote_path,
        direction=data.direction,
        size_bytes=data.size_bytes,
        sha256=digest,
        status=data.status,
        error_code=data.error_code,
        audit_event_id=audit.id,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


def _validate_file_transfer_sha256(data: FileTransferLogCreate) -> str:
    """成功传输必须带 64 位 hex 摘要；失败允许空摘要，拒绝把正文当摘要传入。"""

    digest = data.sha256.strip().lower()
    if data.status == "success":
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise HTTPException(status_code=400, detail="FILE_TRANSFER_SHA256_INVALID")
        return digest
    if digest and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
        raise HTTPException(status_code=400, detail="FILE_TRANSFER_SHA256_INVALID")
    return digest


async def _audit_rejected_command(
    *,
    user: dict[str, Any],
    recording: SessionRecording,
    data: SessionCommandEventCreate,
    decision: CommandDecisionResponse,
) -> None:
    """把拒绝写入审计账本；metadata 只存命令摘要哈希，不落明文。"""

    await audit_service.create_event(
        AuditEventCreate(
            event_type="session.command.rejected",
            category=AuditCategory.policy,
            action="command.reject",
            resource_type="session_recording",
            resource_id=str(recording.id),
            session_id=recording.session_id,
            severity=AuditSeverity.high,
            message="Command rejected by command-filter ACL",
            metadata={
                "reason_code": decision.reason_code,
                "matched_acl_id": decision.matched_acl_id,
                "matched_command_group_id": decision.matched_command_group_id,
                "command_sha256": hashlib.sha256(data.command.encode("utf-8")).hexdigest(),
                "sequence": data.sequence,
                "policy_audit_event_id": decision.audit_event_id,
            },
        ),
        user,
    )


async def _get_scoped_recording(
    *, db: AsyncSession, user: dict[str, Any], recording_id: int
) -> SessionRecording:
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(SessionRecording)
        .where(SessionRecording.id == recording_id)
        .where(SessionRecording.tenant_id == tenant_id)
    )
    recording = result.scalar_one_or_none()
    if recording is None:
        raise HTTPException(status_code=404, detail="SESSION_RECORDING_NOT_FOUND")
    return recording


async def _get_active_scoped_connector(
    *, db: AsyncSession, user: dict[str, Any], connector_id: int
) -> Connector:
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(Connector).where(Connector.id == connector_id).where(Connector.tenant_id == tenant_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=404, detail="CONNECTOR_NOT_FOUND")
    if connector.status != "active":
        raise HTTPException(status_code=403, detail="CONNECTOR_NOT_ACTIVE")
    return connector


def _require_recording_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _require_any_recording_read_permission(user: dict[str, Any]) -> None:
    """文件传输列表对审计员与录制读者均可见，admin 仍作通配符。"""

    permissions = user.get("permissions", [])
    if "admin" in permissions:
        return
    if "session-recordings:read" in permissions or "audit:read" in permissions:
        return
    raise HTTPException(status_code=403, detail="缺少权限: session-recordings:read")


def _ensure_recording_is_open(recording: SessionRecording) -> None:
    if recording.status != "recording":
        raise HTTPException(status_code=404, detail="SESSION_RECORDING_NOT_FOUND")


def _build_command_search_filter(*, query: str, dialect_name: str) -> ColumnElement[bool]:
    if dialect_name == "postgresql":
        document = func.to_tsvector(
            "simple",
            func.concat(SessionCommandEvent.command, " ", SessionCommandEvent.output_excerpt),
        )
        return document.bool_op("@@")(func.plainto_tsquery("simple", query))

    pattern = f"%{query}%"
    return or_(
        SessionCommandEvent.command.ilike(pattern),
        SessionCommandEvent.output_excerpt.ilike(pattern),
    )


def _recording_response(recording: SessionRecording) -> SessionRecordingResponse:
    return SessionRecordingResponse(
        id=recording.id,
        tenant_id=recording.tenant_id,
        session_id=recording.session_id,
        subject_id=recording.subject_id,
        asset_id=recording.asset_id,
        account_id=recording.account_id,
        protocol=recording.protocol,
        status=recording.status,
        storage_uri=recording.storage_uri,
        started_at=_as_utc(recording.started_at),
        ended_at=_as_utc(recording.ended_at),
    )


def _file_transfer_response(log: FileTransferLog) -> FileTransferLogResponse:
    return FileTransferLogResponse(
        id=log.id,
        tenant_id=log.tenant_id,
        recording_id=log.recording_id,
        session_id=log.session_id,
        asset_id=log.asset_id,
        account_id=log.account_id,
        remote_path=log.remote_path,
        direction=log.direction,
        size_bytes=log.size_bytes,
        sha256=log.sha256,
        status=log.status,
        error_code=log.error_code,
        audit_event_id=log.audit_event_id,
        occurred_at=_as_utc(log.occurred_at),
    )


def _command_response(event: SessionCommandEvent) -> SessionCommandEventResponse:
    return SessionCommandEventResponse(
        id=event.id,
        tenant_id=event.tenant_id,
        recording_id=event.recording_id,
        session_id=event.session_id,
        sequence=event.sequence,
        command=event.command,
        exit_code=event.exit_code,
        output_excerpt=event.output_excerpt,
        occurred_at=_as_utc(event.occurred_at),
    )


def _redact_command_excerpt(value: str) -> str:
    return _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
