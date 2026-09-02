"""#t63 RBAC 角色、绑定、用户组与对象级权限模型。

单一角色模型覆盖 system / org 双 scope，禁止 edition 条件分支。内置角色以
``builtin_key`` 标识，持久化记录便于绑定与审计；权限与菜单以 JSON 数组存储，
与仓库既有 ``*_json`` 范式对齐。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RoleScope(StrEnum):
    """角色作用域：system 租户级系统面，org 组织级。"""

    SYSTEM = "system"
    ORG = "org"


class RbacSubjectType(StrEnum):
    """RBAC 绑定主体类型。"""

    USER = "user"
    USER_GROUP = "user_group"


class RoleModel(Base):
    """角色定义：权限集合 + 菜单集合 + scope。"""

    __tablename__ = "rbac_roles"
    __table_args__ = (
        Index(
            "uq_rbac_roles_builtin_per_tenant",
            "tenant_id",
            "builtin_key",
            unique=True,
            postgresql_where=text("builtin_key IS NOT NULL"),
            sqlite_where=text("builtin_key IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[RoleScope] = mapped_column(String(16), nullable=False, default=RoleScope.ORG)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    menu_permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RoleBindingModel(Base):
    """角色绑定：将角色授予 user 或 user_group。"""

    __tablename__ = "rbac_role_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[RbacSubjectType] = mapped_column(
        String(16), nullable=False, default=RbacSubjectType.USER
    )
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserGroupModel(Base):
    """用户组：成员列表供资产授权与 RBAC 绑定复用。"""

    __tablename__ = "rbac_user_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    member_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ObjectPermissionModel(Base):
    """对象级权限：在 RBAC 角色权限之上的细粒度资源动作授权。"""

    __tablename__ = "rbac_object_permissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[RbacSubjectType] = mapped_column(
        String(16), nullable=False, default=RbacSubjectType.USER
    )
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
