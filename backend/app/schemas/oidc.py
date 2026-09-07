"""OIDC settings / login schemas (#t76)."""
from pydantic import BaseModel, ConfigDict, Field


class OidcSettingsResponse(BaseModel):
    enabled: bool = False
    display_name: str = ""
    issuer_url: str = ""
    client_id: str = ""
    client_secret_configured: bool = False
    scopes: str = "openid profile email"
    callback_url: str = ""


class OidcSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    display_name: str = Field(default="", max_length=100)
    issuer_url: str = Field(default="", max_length=512)
    client_id: str = Field(default="", max_length=255)
    client_secret: str | None = Field(default=None, max_length=1024)
    scopes: str = Field(default="openid profile email", max_length=255)


class OidcLoginOptionResponse(BaseModel):
    enabled: bool = False
    display_name: str = ""


class OidcExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: str = Field(min_length=1, max_length=128)
