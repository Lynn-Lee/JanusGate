"""资产相关 Pydantic schemas。"""
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


class PlatformCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    category: str = "host"
    asset_type: AssetTypeLiteral = "host"
    protocols: str = "[]"


class PlatformResponse(BaseModel):
    id: int
    name: str
    category: str
    asset_type: str
    protocols: str
    is_active: bool


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=200)
    platform_id: int
    asset_type: AssetTypeLiteral = "host"
    port: int = 22
    username: str = ""
    description: str = ""
    namespace: str = Field(default="", max_length=253)
    server_ca: str = ""
    zone_id: int | None = None


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address: str | None = Field(default=None, min_length=1, max_length=200)
    port: int | None = None
    username: str | None = None
    description: str | None = None
    namespace: str | None = Field(default=None, max_length=253)
    server_ca: str | None = None
    is_active: bool | None = None
    zone_id: int | None = None


class AssetResponse(BaseModel):
    id: int
    name: str
    address: str
    platform_id: int
    asset_type: str
    port: int
    username: str
    is_active: bool
    description: str
    created_at: str
    namespace: str = ""
    has_server_ca: bool = False
    connect_protocols: list[str] = Field(default_factory=list)
    zone_id: int | None = None


class K8sPodResponse(BaseModel):
    name: str
    containers: list[str] = Field(default_factory=list)


class K8sPodListResponse(BaseModel):
    namespace: str
    items: list[K8sPodResponse]
    total: int
