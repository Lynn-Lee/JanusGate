"""#t67 网域 API schemas。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    gateway_asset_ids: list[int] = Field(default_factory=list)


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    gateway_asset_ids: list[int] | None = None


class ZoneResponse(BaseModel):
    id: int
    name: str
    gateway_count: int
    gateway_asset_ids: list[int] = Field(default_factory=list)


class ZoneListResponse(BaseModel):
    items: list[ZoneResponse]
    total: int
