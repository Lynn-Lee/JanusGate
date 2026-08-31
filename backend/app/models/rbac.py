"""#t63 RBAC 角色与角色绑定模型。

单一角色模型，禁止按 edition 分支（对应关闭历史问题 P1#11 xpack 侵入）。
所有查询强制走租户 scope helper（对应关闭 P2#6 Root 组织无过滤）。

- ``Role``：租户内的自定义角色；内置角色在代码中声明（见 ``app.services.rbac``），
  不落库，因此本表只保存自定义角色。
- ``RoleBinding``：把某个角色（内置 key 或自定义角色 id）授予某个用户，
  ``organization_id`` 为空串表示 system 级绑定，非空表示 org 级绑定。

字段默认 NOT NULL（对应关闭 P2#10 ``null=True`` 反模式）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ROLE_SCOPE_SYSTEM = "system"
ROLE_SCOPE_ORG = "org"


class Role(Base):
    """租户内的自定义角色。内置角色不落库，仅自定义角色写入本表。"""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_SCOPE_SYSTEM)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RoleBinding(Base):
    """把角色授予用户的绑定。``organization_id`` 空串=system 级，非空=org 级。"""

    __tablename__ = "role_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "role_id",
            "organization_id",
            name="uq_role_bindings_subject_role_org",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_SCOPE_SYSTEM)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
