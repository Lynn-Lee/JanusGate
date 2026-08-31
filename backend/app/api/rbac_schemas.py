"""#t63 RBAC 管理 API 的请求/响应 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.rbac import ROLE_SCOPE_ORG, ROLE_SCOPE_SYSTEM

_SCOPE_PATTERN = f"^({ROLE_SCOPE_SYSTEM}|{ROLE_SCOPE_ORG})$"


class RoleResponse(BaseModel):
    """角色（内置或自定义）。``builtin`` 为 true 表示由系统内置、不可删除。"""

    id: str
    tenant_id: str
    name: str
    scope: str
    permissions: list[str]
    description: str
    builtin: bool


class RoleListResponse(BaseModel):
    items: list[RoleResponse]
    total: int


class RoleCreate(BaseModel):
    """创建自定义角色。角色 id 由后端生成。"""

    name: str = Field(min_length=1, max_length=120)
    scope: str = Field(default=ROLE_SCOPE_SYSTEM, pattern=_SCOPE_PATTERN)
    permissions: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=255)


class RoleBindingResponse(BaseModel):
    """角色绑定：把某角色授予某用户。``organization_id`` 空串表示 system 级。"""

    id: str
    tenant_id: str
    user_id: str
    role_id: str
    scope_type: str
    organization_id: str


class RoleBindingListResponse(BaseModel):
    items: list[RoleBindingResponse]
    total: int


class RoleBindingCreate(BaseModel):
    """创建角色绑定。``role_id`` 可为内置角色 key 或自定义角色 id。"""

    user_id: str = Field(min_length=1, max_length=64)
    role_id: str = Field(min_length=1, max_length=64)
    scope_type: str = Field(default=ROLE_SCOPE_SYSTEM, pattern=_SCOPE_PATTERN)
    organization_id: str = Field(default="", max_length=64)
