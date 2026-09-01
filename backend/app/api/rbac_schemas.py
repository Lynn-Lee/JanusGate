"""#t63 RBAC API 请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RoleResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    scope: Literal["system", "org"]
    organization_id: str | None = None
    builtin_key: str | None = None
    permissions: list[str]
    menu_permissions: list[str]
    is_active: bool


class RoleListResponse(BaseModel):
    items: list[RoleResponse]
    total: int


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: Literal["system", "org"] = "org"
    organization_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    menu_permissions: list[str] = Field(default_factory=list)


class RoleBindingResponse(BaseModel):
    id: str
    tenant_id: str
    role_id: str
    subject_type: Literal["user", "user_group"]
    subject_id: str
    organization_id: str | None = None
    is_active: bool


class RoleBindingListResponse(BaseModel):
    items: list[RoleBindingResponse]
    total: int


class RoleBindingCreate(BaseModel):
    role_id: str
    subject_type: Literal["user", "user_group"] = "user"
    subject_id: str
    organization_id: str | None = None


class UserGroupResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    member_ids: list[str]
    is_active: bool


class UserGroupListResponse(BaseModel):
    items: list[UserGroupResponse]
    total: int


class UserGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    member_ids: list[str] = Field(default_factory=list)


class UserGroupMembersUpdate(BaseModel):
    member_ids: list[str]


class ObjectPermissionResponse(BaseModel):
    id: str
    tenant_id: str
    subject_type: Literal["user", "user_group"]
    subject_id: str
    resource_type: str
    resource_id: str
    action: str
    organization_id: str | None = None
    is_active: bool


class ObjectPermissionListResponse(BaseModel):
    items: list[ObjectPermissionResponse]
    total: int


class ObjectPermissionCreate(BaseModel):
    subject_type: Literal["user", "user_group"] = "user"
    subject_id: str
    resource_type: str
    resource_id: str
    action: str
    organization_id: str | None = None


class EffectiveRbacResponse(BaseModel):
    permissions: list[str]
    menu_permissions: list[str]
    group_ids: list[str]
    role_ids: list[str]
