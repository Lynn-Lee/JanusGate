"""Phase 4 SSH CA temporary certificate API schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class SshCertificateIssueRequest(BaseModel):
    ca_id: int
    asset_id: int
    account_id: int
    principal: str = Field(min_length=1, max_length=120)
    public_key: str = Field(min_length=1)


class SshCertificateRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=240)


class SshCertificateResponse(BaseModel):
    id: int
    tenant_id: str
    ca_id: int
    asset_id: int
    account_id: int
    principal: str
    public_key: str
    serial: str
    status: str
    certificate_body: str
    requested_by: str
    valid_after: datetime
    valid_before: datetime
    revoked_at: datetime | None
    revoke_reason: str | None


class SshCertificateListResponse(BaseModel):
    items: list[SshCertificateResponse]
    total: int
