"""#t63 RBAC 角色、绑定与对象级权限模型。

单一 Role 模型覆盖 system / organization 双 scope；内置角色不可删改权限集合。
查询强制走 scoped_select，跨租户管理请求按 404 处理。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

SCOPE_SYSTEM = "system"
SCOPE_ORGANIZATION = "organization"
SUBJECT_USER = "user"
SUBJECT_USER_GROUP = "user_group"


class RoleModel(Base):
    """租户内角色定义，含全局权限与菜单权限 JSON。"""

    __tablename__ = "roles"
    __table_args__ = (
        Index(
            "uq_roles_tenant_scope_name",
            "tenant_id",
            "scope_type",
            "organization_id",
            "name",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    builtin_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    menu_permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RoleBindingModel(Base):
    """将角色绑定到用户或用户组，可带 organization scope。"""

    __tablename__ = "role_bindings"
    __table_args__ = (
        Index(
            "uq_role_bindings_subject_role",
            "tenant_id",
            "role_id",
            "subject_type",
            "subject_id",
            "scope_type",
            "organization_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RoleObjectPermissionModel(Base):
    """对象级权限：限定角色对特定资源的动作。"""

    __tablename__ = "role_object_permissions"
    __table_args__ = (
        Index(
            "uq_role_object_permissions_unique",
            "tenant_id",
            "role_id",
            "resource_type",
            "resource_id",
            "action",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
