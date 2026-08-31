"""#t63 RBAC 服务：内置角色定义与用户有效权限解析。

设计要点：

- **单一角色模型**：内置角色在此以代码常量声明，不按 edition 分支（关闭 P1#11）。
- **迁移路径**：历史上权限由 ``is_superuser`` 直接映射为 ``admin`` / ``assets:read``
  字符串（见 ``app.api.auth``）。本服务保留该映射为「无显式角色绑定时的回退基线」，
  并在其之上并入显式 ``RoleBinding`` 授予的权限，因此接入 RBAC 不会削弱既有账号权限。
- **租户过滤**：解析绑定时强制带 ``tenant_id`` 过滤（关闭 P2#6）。

内置角色权限集合与 ``app.api.auth`` 的历史常量保持一致，避免行为漂移。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import ROLE_SCOPE_ORG, ROLE_SCOPE_SYSTEM, Role, RoleBinding

logger = logging.getLogger(__name__)

# 系统管理员：等价于历史 ADMIN_CONSOLE_PERMISSIONS，并补齐 RBAC / 租户管理权限。
_SYSTEM_ADMIN_PERMISSIONS: tuple[str, ...] = (
    "admin",
    "assets:read",
    "assets:write",
    "assets:test",
    "audit:read",
    "audit:write",
    "sessions:connect",
    "workflow:approve",
    "workflow:audit",
    "workflow:admin",
    "tenancy:read",
    "rbac:read",
    "rbac:admin",
)

# 普通用户：等价于历史 MVP_CONSOLE_PERMISSIONS。
_DEFAULT_USER_PERMISSIONS: tuple[str, ...] = ("assets:read",)


@dataclass(frozen=True)
class BuiltinRole:
    """内置角色定义。``key`` 同时用作 ``RoleBinding.role_id`` 的取值。"""

    key: str
    name: str
    scope: str
    permissions: tuple[str, ...]
    description: str = ""
    builtin: bool = field(default=True)


BUILTIN_ROLES: dict[str, BuiltinRole] = {
    "system_admin": BuiltinRole(
        key="system_admin",
        name="系统管理员",
        scope=ROLE_SCOPE_SYSTEM,
        permissions=_SYSTEM_ADMIN_PERMISSIONS,
        description="租户内的最高权限角色，可管理资产、审计、工单、RBAC 与租户结构。",
    ),
    "org_admin": BuiltinRole(
        key="org_admin",
        name="组织管理员",
        scope=ROLE_SCOPE_ORG,
        permissions=(
            "assets:read",
            "assets:write",
            "assets:test",
            "sessions:connect",
            "workflow:approve",
            "workflow:audit",
            "workflow:admin",
            "audit:read",
            "tenancy:read",
            "rbac:read",
        ),
        description="组织范围管理员，可管理组织内资产与工单，但不具备全局 admin 能力。",
    ),
    "auditor": BuiltinRole(
        key="auditor",
        name="审计员",
        scope=ROLE_SCOPE_SYSTEM,
        permissions=("audit:read", "assets:read"),
        description="只读审计角色，可查看审计事件与资产清单。",
    ),
    "user": BuiltinRole(
        key="user",
        name="普通用户",
        scope=ROLE_SCOPE_SYSTEM,
        permissions=_DEFAULT_USER_PERMISSIONS,
        description="默认业务用户，可查看被授权的资产并发起 JIT 申请。",
    ),
}


def legacy_fallback_permissions(*, is_superuser: bool) -> tuple[str, ...]:
    """无显式角色绑定时的权限回退基线（保持历史行为不变）。"""

    if is_superuser:
        return _SYSTEM_ADMIN_PERMISSIONS
    return _DEFAULT_USER_PERMISSIONS


def _decode_permissions(raw: str) -> list[str]:
    """解析自定义角色的权限 JSON，异常时安全返回空集并记录结构化日志。"""

    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        logger.warning("rbac.role.permissions_json_invalid", extra={"raw_len": len(raw or "")})
        return []
    if not isinstance(parsed, list):
        logger.warning("rbac.role.permissions_json_not_list")
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


class RbacService:
    """RBAC 权限解析服务。所有绑定查询强制带租户过滤。"""

    @staticmethod
    async def resolve_effective_permissions(
        db: AsyncSession,
        *,
        user_id: str,
        tenant_id: str,
        is_superuser: bool,
    ) -> list[str]:
        """解析用户有效权限。

        规则：显式 ``RoleBinding`` 授予的权限，与「``is_superuser`` 回退基线」求并集。
        因此显式角色只会**新增**权限，不会移除既有账号的历史权限，保证平滑迁移。
        """

        effective: set[str] = set(legacy_fallback_permissions(is_superuser=is_superuser))

        bindings_result = await db.execute(
            select(RoleBinding).where(
                RoleBinding.tenant_id == tenant_id,
                RoleBinding.user_id == str(user_id),
            )
        )
        bindings: Sequence[RoleBinding] = bindings_result.scalars().all()
        if not bindings:
            return sorted(effective)

        custom_role_ids: set[str] = set()
        for binding in bindings:
            builtin = BUILTIN_ROLES.get(binding.role_id)
            if builtin is not None:
                effective.update(builtin.permissions)
            else:
                custom_role_ids.add(binding.role_id)

        if custom_role_ids:
            roles_result = await db.execute(
                select(Role).where(
                    Role.tenant_id == tenant_id,
                    Role.id.in_(custom_role_ids),
                )
            )
            for role in roles_result.scalars().all():
                effective.update(_decode_permissions(role.permissions_json))

        return sorted(effective)
