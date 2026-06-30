"""Schemas for Connector API v2."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ConnectorCapability(StrEnum):
    SSH = "ssh"
    RDP = "rdp"
    DATABASE = "database"
    KUBERNETES = "kubernetes"


class ConnectorStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"


class ConnectorRegistrationRequest(BaseModel):
    name: str
    environment: str
    public_key_fingerprint: str
    capabilities: list[ConnectorCapability]
    enrollment_token: str


class ConnectorRecord(BaseModel):
    id: str
    name: str
    environment: str
    public_key_fingerprint: str
    capabilities: list[ConnectorCapability]
    status: ConnectorStatus = ConnectorStatus.ACTIVE
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime | None = None


class ConnectionToken(BaseModel):
    connector_id: str
    token: str
    ttl_seconds: int
    policy_audit_event_id: str
