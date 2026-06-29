"""资产相关 Pydantic schemas。"""
from pydantic import BaseModel, Field


class PlatformCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    category: str = "host"
    protocols: str = "[]"


class PlatformResponse(BaseModel):
    id: int
    name: str
    category: str
    protocols: str
    is_active: bool


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=200)
    platform_id: int
    port: int = 22
    username: str = ""
    description: str = ""


class AssetResponse(BaseModel):
    id: int
    name: str
    address: str
    platform_id: int
    port: int
    username: str
    is_active: bool
    description: str
    created_at: str
