"""认证相关 Pydantic schemas。"""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "Bearer"
    requires_2fa: bool = False
    two_fa_token: str = ""


class TwoFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TwoFAVerifyRequest(BaseModel):
    totp_code: str = Field(min_length=6, max_length=6)


class Login2FARequest(BaseModel):
    two_fa_token: str
    totp_code: str = Field(min_length=6, max_length=6)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyResponse(BaseModel):
    key_id: str
    secret: str
    name: str
    created_at: str


class ApiKeyListResponse(BaseModel):
    id: int
    key_id: str
    name: str
    is_active: bool
    last_used_at: str | None
    created_at: str


class UserMeResponse(BaseModel):
    id: int
    username: str
    display_name: str
    email: str
    is_superuser: bool
    totp_enabled: bool
