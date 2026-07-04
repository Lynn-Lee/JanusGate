"""Schemas for persistent connector management API."""
from datetime import datetime
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


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    environment: str = Field(min_length=1, max_length=64)
    public_key_fingerprint: str = Field(min_length=8, max_length=160)
    mtls_certificate_fingerprint: str | None = Field(default=None, max_length=160)
    attestation_nonce: str | None = Field(default=None, max_length=160)
    attestation_digest: str | None = Field(default=None, max_length=160)
    capabilities: list[ConnectorCapability]
    status: ConnectorStatus = ConnectorStatus.ACTIVE


class ConnectorKeyRotationRequest(BaseModel):
    public_key_fingerprint: str = Field(min_length=8, max_length=160)


class ConnectorResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    environment: str
    public_key_fingerprint: str
    previous_public_key_fingerprint: str | None
    capabilities: list[ConnectorCapability]
    status: ConnectorStatus
    mtls_bound: bool
    attestation_bound: bool
    registered_at: datetime | None
    last_heartbeat_at: datetime | None
    key_rotated_at: datetime | None


class ConnectorListResponse(BaseModel):
    items: list[ConnectorResponse]
    total: int
