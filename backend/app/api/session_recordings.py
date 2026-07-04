"""Phase 4 session recording and command search API routes."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.session_recording_schemas import (
    SessionCommandEventCreate,
    SessionCommandEventListResponse,
    SessionCommandEventResponse,
    SessionRecordingCreate,
    SessionRecordingResponse,
)
from app.core.database import get_db
from app.core.deps import current_user
from app.models.session_recording import SessionCommandEvent, SessionRecording

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
    event = SessionCommandEvent(
        tenant_id=recording.tenant_id,
        recording_id=recording.id,
        session_id=recording.session_id,
        sequence=data.sequence,
        command=data.command,
        exit_code=data.exit_code,
        output_excerpt=_redact_command_excerpt(data.output_excerpt),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return _command_response(event)


@router.get(
    "/session-recordings/{recording_id}/commands",
    response_model=SessionCommandEventListResponse,
)
async def list_session_recording_commands(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
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


@router.get("/session-recordings/commands", response_model=SessionCommandEventListResponse)
async def search_session_commands(
    query: str = Query(min_length=1, max_length=120),
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SessionCommandEventListResponse:
    _require_recording_permission(user, "session-recordings:read")
    tenant_id = str(user.get("tenant_id") or "default")
    pattern = f"%{query}%"
    result = await db.execute(
        select(SessionCommandEvent)
        .where(SessionCommandEvent.tenant_id == tenant_id)
        .where(
            or_(
                SessionCommandEvent.command.ilike(pattern),
                SessionCommandEvent.output_excerpt.ilike(pattern),
            )
        )
        .order_by(SessionCommandEvent.occurred_at.desc(), SessionCommandEvent.id.desc())
    )
    events = result.scalars().all()
    items = [_command_response(event) for event in events]
    return SessionCommandEventListResponse(items=items, total=len(items))


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


def _require_recording_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


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
