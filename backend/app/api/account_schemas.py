"""Phase 4 account custody API schemas."""
from datetime import datetime

from pydantic import BaseModel, Field

# #t68 Design + Product：TTL 默认 900，API 校验 60..3600（非 86400）。
TOKEN_TTL_DEFAULT = 900
TOKEN_TTL_MIN = 60
TOKEN_TTL_MAX = 3600


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
    use_token_request: bool = False
    token_ttl_seconds: int = Field(default=TOKEN_TTL_DEFAULT, ge=TOKEN_TTL_MIN, le=TOKEN_TTL_MAX)


class AccountUpdate(BaseModel):
    secret_id: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, min_length=1, max_length=20)
    rotation_policy: str | None = Field(default=None, min_length=1, max_length=40)
    organization_id: str | None = Field(default=None, min_length=1, max_length=64)
    team_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    use_token_request: bool | None = None
    token_ttl_seconds: int | None = Field(default=None, ge=TOKEN_TTL_MIN, le=TOKEN_TTL_MAX)


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
    use_token_request: bool = False
    token_ttl_seconds: int = TOKEN_TTL_DEFAULT


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
