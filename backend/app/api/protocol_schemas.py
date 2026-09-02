"""#t66 协议与资产类型 API schemas。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AssetTypeLiteral = Literal[
    "host",
    "database",
    "device",
    "web",
    "cloud",
    "custom",
    "directory_service",
    "gpt",
]


class ProtocolResponse(BaseModel):
    id: str
    name: str
    category: str
    default_port: int
    asset_types: list[str]
    credential_types: list[str]
    driver_module: str | None
    is_builtin: bool


class ProtocolListResponse(BaseModel):
    items: list[ProtocolResponse]
    total: int


class PlatformProtocolResponse(BaseModel):
    protocol_id: str
    name: str
    category: str
    port: int
    credential_types: list[str]
    driver_module: str | None
    is_primary: bool
    settings: dict[str, object] = Field(default_factory=dict)


class PlatformProtocolListResponse(BaseModel):
    platform_id: int
    asset_type: str
    items: list[PlatformProtocolResponse]
    total: int


class AssetTypeProtocolsResponse(BaseModel):
    asset_type: AssetTypeLiteral
    items: list[ProtocolResponse]
    total: int
