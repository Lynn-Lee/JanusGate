"""Session Gateway request/response schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.sessions.service import SessionRecord, SessionStatus


class SessionCreateRequest(BaseModel):
    asset_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    protocol: str = Field(min_length=1, max_length=32)
    connection_token: str = Field(min_length=1)
    client_ip: str = ""


class SessionCloseRequest(BaseModel):
    reason: str = Field(default="user_requested", min_length=1, max_length=100)


class SessionResponse(BaseModel):
    id: str
    asset_id: str
    account_id: str
    connector_id: str
    protocol: str
    status: SessionStatus
    connection_url: str
    created_at: str
    updated_at: str
    closed_at: str | None = None
    audit_event_ids: list[str]

    @classmethod
    def from_record(cls, session: SessionRecord) -> SessionResponse:
        return cls(
            id=session.id,
            asset_id=session.asset_id,
            account_id=session.account_id,
            connector_id=session.connector_id,
            protocol=session.protocol,
            status=session.status,
            connection_url=session.connection_url,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            closed_at=session.closed_at.isoformat() if session.closed_at else None,
            audit_event_ids=session.audit_event_ids,
        )
