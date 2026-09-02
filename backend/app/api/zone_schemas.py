"""#t67 网域与网关 API schemas。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class ZoneResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    is_active: bool
    gateway_count: int = 0


class ZoneListResponse(BaseModel):
    items: list[ZoneResponse]


class ZoneGatewayCreate(BaseModel):
    gateway_asset_id: int
    gateway_account_id: int | None = None


class ZoneGatewayResponse(BaseModel):
    id: int
    zone_id: str
    gateway_asset_id: int
    gateway_account_id: int | None
    is_active: bool
    last_probe_at: str | None
    probe_status: str
    probe_error: str


class ZoneGatewayListResponse(BaseModel):
    items: list[ZoneGatewayResponse]


class ZoneGatewayProbeResponse(BaseModel):
    gateway_asset_id: int
    probe_status: str
    probe_error: str
    last_probe_at: str | None
