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
