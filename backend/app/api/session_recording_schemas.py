"""Session recording request and response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionRecordingCreate(BaseModel):
    asset_id: str = Field(min_length=1, max_length=120)
    account_id: str = Field(min_length=1, max_length=120)
    protocol: str = Field(min_length=1, max_length=32)
    storage_uri: str = Field(min_length=1, max_length=512)


class SessionRecordingResponse(BaseModel):
    id: int
    tenant_id: str
    session_id: str
    subject_id: str
    asset_id: str
    account_id: str
    protocol: str
    status: str
    storage_uri: str
    started_at: datetime | None
    ended_at: datetime | None


class SessionCommandEventCreate(BaseModel):
    sequence: int = Field(ge=0)
    command: str = Field(min_length=1, max_length=4096)
    exit_code: int | None = None
    output_excerpt: str = Field(default="", max_length=4096)


class SessionCommandEventResponse(BaseModel):
    id: int
    tenant_id: str
    recording_id: int
    session_id: str
    sequence: int
    command: str
    exit_code: int | None
    output_excerpt: str
    occurred_at: datetime | None


class SessionCommandEventListResponse(BaseModel):
    items: list[SessionCommandEventResponse]
    total: int
