"""#t64 资产树 / AssetPermission 管理 API schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: str | None = None


class NodeRename(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class NodeMove(BaseModel):
    parent_id: str


class HangAsset(BaseModel):
    asset_id: int


class UngroupAsset(BaseModel):
    asset_id: int


class PermissionCreate(BaseModel):
    subject_id: str = Field(min_length=1, max_length=64)
    subject_type: Literal["user", "user_group"] = "user"
    action: str = Field(default="connect", min_length=1, max_length=32)
    expires_at: datetime | None = None
    account_id: str = ""
    protocol: str = ""
    from_ticket: str | None = Field(default=None, max_length=128)


class NodeResponse(BaseModel):
    id: str
    tenant_id: str
    parent_id: str | None
    name: str
    is_root: bool
    ancestor_ids: list[str]


class NodeListResponse(BaseModel):
    items: list[NodeResponse]


class TreeAssetResponse(BaseModel):
    id: int
    name: str
    address: str
    node_id: str | None
    location_label: str


class TreeAssetListResponse(BaseModel):
    items: list[TreeAssetResponse]


class PermissionResponse(BaseModel):
    id: str
    tenant_id: str
    subject_id: str
    subject_type: str
    resource_type: str
    resource_id: str
    account_id: str
    protocol: str
    action: str
    expires_at: datetime | None
    from_ticket: str | None
    expired: bool
    inherited: bool
    inherited_from_node_id: str | None
    inherited_from_node_name: str | None


class PermissionListResponse(BaseModel):
    items: list[PermissionResponse]


class ConnectImpactResponse(BaseModel):
    lost: list[dict[str, str]]
