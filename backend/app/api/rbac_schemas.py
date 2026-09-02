"""#t63 RBAC API schemas。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.rbac import SCOPE_SYSTEM


class RoleResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    display_name: str
    scope_type: Literal["system", "organization"]
    organization_id: str | None
    is_builtin: bool
    builtin_key: str | None
    description: str
    permissions: list[str]
    menu_permissions: list[str]


class RoleListResponse(BaseModel):
    items: list[RoleResponse]
    total: int


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    scope_type: Literal["system", "organization"] = SCOPE_SYSTEM
    organization_id: str | None = None
    description: str = ""
    permissions: list[str] = Field(default_factory=list)
    menu_permissions: list[str] = Field(default_factory=list)

    @field_validator("organization_id", mode="before")
    @classmethod
    def empty_org_to_none(cls, value: object) -> str | None:
        if value in ("", None):
            return None
        return str(value)


class RoleUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    permissions: list[str] | None = None
    menu_permissions: list[str] | None = None


class RoleBindingResponse(BaseModel):
    id: str
    tenant_id: str
    role_id: str
    subject_type: Literal["user", "user_group"]
    subject_id: str
    scope_type: Literal["system", "organization"]
    organization_id: str | None


class RoleBindingListResponse(BaseModel):
    items: list[RoleBindingResponse]
    total: int


class RoleBindingCreate(BaseModel):
    role_id: str
    subject_type: Literal["user", "user_group"] = "user"
    subject_id: str = Field(min_length=1, max_length=64)
    scope_type: Literal["system", "organization"] = SCOPE_SYSTEM
    organization_id: str | None = None

    @field_validator("organization_id", mode="before")
    @classmethod
    def empty_org_to_none(cls, value: object) -> str | None:
        if value in ("", None):
            return None
        return str(value)


class ObjectPermissionResponse(BaseModel):
    resource_type: str
    resource_id: str
    action: str


class EffectiveRbacResponse(BaseModel):
    permissions: list[str]
    menu_permissions: list[str]
    role_ids: list[str]
    object_permissions: list[ObjectPermissionResponse]
