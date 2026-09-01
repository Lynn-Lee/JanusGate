"""#t63 RBAC 权限解析、内置角色与用户组成员解析。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import (
    ObjectPermissionModel,
    RbacSubjectType,
    RoleBindingModel,
    RoleModel,
    RoleScope,
    UserGroupModel,
)
from app.tenancy.scope import ActorScope, scoped_select

# 与 auth.py 历史常量对齐，作为内置角色权限基线。
MVP_CONSOLE_PERMISSIONS: tuple[str, ...] = ("assets:read", "sessions:connect", "workflow:request")
ADMIN_CONSOLE_PERMISSIONS: tuple[str, ...] = (
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
    "rbac:read",
    "rbac:write",
)

MVP_CONSOLE_MENUS: tuple[str, ...] = ("assets", "sessions", "workflow")
ADMIN_CONSOLE_MENUS: tuple[str, ...] = (
    "assets",
    "sessions",
    "workflow",
    "audits",
    "settings",
    "tenancy",
    "accounts",
    "ssh-ca",
    "rbac",
)

BUILTIN_ROLE_TEMPLATES: dict[str, dict[str, Any]] = {
    "system_admin": {
        "name": "系统管理员",
        "scope": RoleScope.SYSTEM,
        "permissions": ADMIN_CONSOLE_PERMISSIONS,
        "menus": ADMIN_CONSOLE_MENUS,
    },
    "org_admin": {
        "name": "组织管理员",
        "scope": RoleScope.ORG,
        "permissions": (
            "admin",
            "assets:read",
            "assets:write",
            "assets:test",
            "audit:read",
            "sessions:connect",
            "workflow:approve",
            "workflow:audit",
            "workflow:admin",
            "rbac:read",
            "rbac:write",
        ),
        "menus": ADMIN_CONSOLE_MENUS,
    },
    "auditor": {
        "name": "审计员",
        "scope": RoleScope.ORG,
        "permissions": ("audit:read", "workflow:audit", "sessions:connect"),
        "menus": ("audits", "sessions", "workflow"),
    },
    "user": {
        "name": "普通用户",
        "scope": RoleScope.ORG,
        "permissions": MVP_CONSOLE_PERMISSIONS,
        "menus": MVP_CONSOLE_MENUS,
    },
}


@dataclass(frozen=True)
class EffectiveRbac:
    """用户在某租户下的有效 RBAC 快照。"""

    permissions: tuple[str, ...]
    menu_permissions: tuple[str, ...]
    group_ids: tuple[str, ...]
    role_ids: tuple[str, ...]


def dump_json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def parse_json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item is not None and str(item)]


def new_rbac_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class RbacService:
    """解析角色绑定、用户组成员与对象级权限。"""

    @staticmethod
    async def ensure_builtin_roles(db: AsyncSession, tenant_id: str) -> None:
        """为租户补全内置角色记录（幂等）。"""
        existing = await db.execute(
            select(RoleModel.builtin_key).where(
                RoleModel.tenant_id == tenant_id,
                RoleModel.builtin_key.is_not(None),
            )
        )
        present = {key for key in existing.scalars().all() if key}
        added = False
        for builtin_key, template in BUILTIN_ROLE_TEMPLATES.items():
            if builtin_key in present:
                continue
            db.add(
                RoleModel(
                    id=new_rbac_id("role"),
                    tenant_id=tenant_id,
                    name=str(template["name"]),
                    scope=template["scope"],
                    builtin_key=builtin_key,
                    permissions_json=dump_json_list(list(template["permissions"])),
                    menu_permissions_json=dump_json_list(list(template["menus"])),
                )
            )
            added = True
        if added:
            await db.commit()
        else:
            await db.flush()

    @staticmethod
    async def resolve_effective_rbac(
        db: AsyncSession,
        *,
        user_id: str,
        tenant_id: str,
        is_superuser: bool = False,
        organization_id: str | None = None,
    ) -> EffectiveRbac:
        """合并内置回退、角色绑定与用户组成员。"""
        await RbacService.ensure_builtin_roles(db, tenant_id)
        actor_scope = ActorScope(user_id=user_id, tenant_id=tenant_id)
        group_ids = await RbacService.list_group_ids_for_user(db, actor_scope, user_id)
        role_ids = await RbacService.list_role_ids_for_subject(
            db,
            actor_scope,
            subject_id=user_id,
            subject_type=RbacSubjectType.USER,
            group_ids=group_ids,
            organization_id=organization_id,
        )

        permissions: set[str] = set()
        menus: set[str] = set()
        if role_ids:
            roles = await db.execute(
                scoped_select(RoleModel, actor_scope).where(
                    RoleModel.id.in_(role_ids),
                    RoleModel.is_active.is_(True),
                )
            )
            for role in roles.scalars().all():
                permissions.update(parse_json_list(role.permissions_json))
                menus.update(parse_json_list(role.menu_permissions_json))
        elif is_superuser:
            permissions.update(ADMIN_CONSOLE_PERMISSIONS)
            menus.update(ADMIN_CONSOLE_MENUS)
        else:
            permissions.update(MVP_CONSOLE_PERMISSIONS)
            menus.update(MVP_CONSOLE_MENUS)

        return EffectiveRbac(
            permissions=tuple(sorted(permissions)),
            menu_permissions=tuple(sorted(menus)),
            group_ids=group_ids,
            role_ids=tuple(sorted(role_ids)),
        )

    @staticmethod
    async def list_group_ids_for_user(
        db: AsyncSession, actor_scope: ActorScope, user_id: str
    ) -> tuple[str, ...]:
        result = await db.execute(
            scoped_select(UserGroupModel, actor_scope).where(UserGroupModel.is_active.is_(True))
        )
        matched: list[str] = []
        for group in result.scalars().all():
            if user_id in parse_json_list(group.member_ids_json):
                matched.append(group.id)
        return tuple(sorted(matched))

    @staticmethod
    async def list_role_ids_for_subject(
        db: AsyncSession,
        actor_scope: ActorScope,
        *,
        subject_id: str,
        subject_type: RbacSubjectType,
        group_ids: tuple[str, ...] = (),
        organization_id: str | None = None,
    ) -> tuple[str, ...]:
        bindings = await db.execute(
            scoped_select(RoleBindingModel, actor_scope).where(
                RoleBindingModel.is_active.is_(True),
            )
        )
        role_ids: set[str] = set()
        for binding in bindings.scalars().all():
            if binding.subject_type == RbacSubjectType.USER:
                if binding.subject_id != subject_id:
                    continue
            elif binding.subject_type == RbacSubjectType.USER_GROUP:
                if binding.subject_id not in group_ids:
                    continue
            else:
                continue
            if not _binding_matches_org(binding, organization_id):
                continue
            role_ids.add(binding.role_id)
        return tuple(sorted(role_ids))

    @staticmethod
    async def has_object_permission(
        db: AsyncSession,
        actor_scope: ActorScope,
        *,
        subject_id: str,
        group_ids: tuple[str, ...],
        resource_type: str,
        resource_id: str,
        action: str,
        organization_id: str | None = None,
    ) -> bool:
        """对象级权限：命中任一 active 记录即允许。"""
        result = await db.execute(
            scoped_select(ObjectPermissionModel, actor_scope).where(
                ObjectPermissionModel.is_active.is_(True),
                ObjectPermissionModel.resource_type == resource_type,
                ObjectPermissionModel.resource_id == resource_id,
                ObjectPermissionModel.action == action,
            )
        )
        for permission in result.scalars().all():
            if not _binding_matches_org(permission, organization_id):
                continue
            if permission.subject_type == RbacSubjectType.USER and permission.subject_id == subject_id:
                return True
            if (
                permission.subject_type == RbacSubjectType.USER_GROUP
                and permission.subject_id in group_ids
            ):
                return True
        return False

    @staticmethod
    def permissions_include(actor_permissions: list[str] | tuple[str, ...], perm: str) -> bool:
        if "admin" in actor_permissions:
            return True
        return perm in actor_permissions


def _binding_matches_org(record: Any, organization_id: str | None) -> bool:
    record_org = getattr(record, "organization_id", None)
    if record_org is None:
        return True
    if organization_id is None:
        return False
    return record_org == organization_id
