"""Phase 4 account custody API schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    asset_id: int
    username: str = Field(min_length=1, max_length=100)
    protocol: str = Field(default="ssh", min_length=1, max_length=32)
    secret_id: str = Field(min_length=1, max_length=120)
    organization_id: str | None = Field(default=None, min_length=1, max_length=64)
    team_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: str = Field(default="active", min_length=1, max_length=20)
    rotation_policy: str = Field(default="manual", min_length=1, max_length=40)
    k8s_namespaces: list[str] = Field(default_factory=list)
    k8s_service_account: str = Field(default="default", min_length=1, max_length=253)
    k8s_default_pod: str = Field(default="", max_length=253)
    k8s_default_container: str | None = Field(default=None, max_length=253)
    k8s_use_short_lived_token: bool = True
    k8s_token_ttl_seconds: int = Field(default=3600, ge=600, le=86400)


class AccountResponse(BaseModel):
    id: int
    tenant_id: str
    asset_id: int
    username: str
    protocol: str
    secret_id: str
    organization_id: str | None
    team_id: str | None
    project_id: str | None
    status: str
    rotation_policy: str
    k8s_namespaces: list[str] = Field(default_factory=list)
    k8s_service_account: str = "default"
    k8s_default_pod: str = ""
    k8s_default_container: str | None = None
    k8s_use_short_lived_token: bool = True
    k8s_token_ttl_seconds: int = 3600


class AccountListResponse(BaseModel):
    items: list[AccountResponse]
    total: int


class CredentialRotationCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=240)
    scheduled_at: datetime | None = None


class CredentialRotationResponse(BaseModel):
    id: int
    tenant_id: str
    account_id: int
    status: str
    reason: str | None
    requested_by: str
    scheduled_at: datetime | None


class CredentialRotationListResponse(BaseModel):
    items: list[CredentialRotationResponse]
    total: int
