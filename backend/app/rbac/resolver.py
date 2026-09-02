"""#t63 RBAC 有效权限解析。"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import (
    SCOPE_ORGANIZATION,
    SCOPE_SYSTEM,
    SUBJECT_USER,
    SUBJECT_USER_GROUP,
    RoleBindingModel,
    RoleModel,
    RoleObjectPermissionModel,
)
from app.rbac.constants import MVP_CONSOLE_PERMISSIONS
from app.rbac.ops import load_json_list
from app.rbac.repository import ensure_builtin_roles, list_object_permissions_for_roles
from app.tenancy.scope import ActorScope, scoped_select


@dataclass(frozen=True)
class ObjectPermission:
    resource_type: str
    resource_id: str
    action: str


@dataclass(frozen=True)
class EffectiveRbac:
    permissions: tuple[str, ...]
    menu_permissions: tuple[str, ...]
    role_ids: tuple[str, ...]
    object_permissions: tuple[ObjectPermission, ...] = field(default_factory=tuple)


class RbacResolver:
    """按租户、主体与组织上下文合并角色权限。"""

    @staticmethod
    async def resolve(
        db: AsyncSession,
        *,
        actor_scope: ActorScope,
        group_ids: tuple[str, ...] = (),
        is_superuser: bool = False,
    ) -> EffectiveRbac:
        await ensure_builtin_roles(db, actor_scope.tenant_id)
        if is_superuser:
            return await RbacResolver._resolve_superuser(db, actor_scope=actor_scope)

        bindings = await RbacResolver._load_bindings(db, actor_scope, group_ids)
        if not bindings:
            return RbacResolver._default_user_effective()

        role_ids = tuple(sorted({binding.role_id for binding in bindings}))
        roles = await RbacResolver._load_roles(db, actor_scope, role_ids)
        role_by_id = {role.id: role for role in roles}
        object_permissions = await list_object_permissions_for_roles(
            db, actor_scope, role_ids
        )

        permissions: set[str] = set()
        menus: set[str] = set()
        effective_objects: list[ObjectPermission] = []

        for binding in bindings:
            role = role_by_id.get(binding.role_id)
            if role is None:
                continue
            if not RbacResolver._binding_applies(binding, actor_scope):
                continue
            permissions.update(load_json_list(role.permissions_json))
            menus.update(load_json_list(role.menu_permissions_json))

        for item in object_permissions:
            if not RbacResolver._object_permission_applies(item, actor_scope):
                continue
            effective_objects.append(
                ObjectPermission(
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    action=item.action,
                )
            )
            permissions.add(item.action)

        if not permissions:
            return RbacResolver._default_user_effective()

        return EffectiveRbac(
            permissions=tuple(sorted(permissions)),
            menu_permissions=tuple(sorted(menus)),
            role_ids=role_ids,
            object_permissions=tuple(effective_objects),
        )

    @staticmethod
    async def _resolve_superuser(
        db: AsyncSession, *, actor_scope: ActorScope
    ) -> EffectiveRbac:
        result = await db.execute(
            scoped_select(RoleModel, actor_scope).where(
                RoleModel.builtin_key == "system_admin"
            )
        )
        role = result.scalar_one_or_none()
        if role is None:
            return EffectiveRbac(
                permissions=MVP_CONSOLE_PERMISSIONS,
                menu_permissions=(),
                role_ids=(),
            )
        return EffectiveRbac(
            permissions=tuple(sorted(load_json_list(role.permissions_json))),
            menu_permissions=tuple(sorted(load_json_list(role.menu_permissions_json))),
            role_ids=(role.id,),
        )

    @staticmethod
    def _default_user_effective() -> EffectiveRbac:
        return EffectiveRbac(
            permissions=MVP_CONSOLE_PERMISSIONS,
            menu_permissions=("dashboard", "assets", "sessions"),
            role_ids=(),
        )

    @staticmethod
    async def _load_bindings(
        db: AsyncSession,
        actor_scope: ActorScope,
        group_ids: tuple[str, ...],
    ) -> list[RoleBindingModel]:
        subject_ids = [actor_scope.user_id, *group_ids]
        statement = scoped_select(RoleBindingModel, actor_scope).where(
            RoleBindingModel.subject_id.in_(subject_ids)
        )
        result = await db.execute(statement)
        bindings = list(result.scalars().all())
        allowed_subjects: set[tuple[str, str]] = {(SUBJECT_USER, actor_scope.user_id)}
        allowed_subjects.update((SUBJECT_USER_GROUP, group_id) for group_id in group_ids)
        return [
            binding
            for binding in bindings
            if (binding.subject_type, binding.subject_id) in allowed_subjects
        ]

    @staticmethod
    async def _load_roles(
        db: AsyncSession, actor_scope: ActorScope, role_ids: tuple[str, ...]
    ) -> list[RoleModel]:
        if not role_ids:
            return []
        result = await db.execute(
            scoped_select(RoleModel, actor_scope).where(RoleModel.id.in_(role_ids))
        )
        return list(result.scalars().all())

    @staticmethod
    def _binding_applies(binding: RoleBindingModel, actor_scope: ActorScope) -> bool:
        if binding.scope_type == SCOPE_SYSTEM:
            return True
        if binding.scope_type != SCOPE_ORGANIZATION:
            return False
        if binding.organization_id is None:
            return bool(actor_scope.organization_ids)
        return binding.organization_id in actor_scope.organization_ids

    @staticmethod
    def _object_permission_applies(
        item: RoleObjectPermissionModel, actor_scope: ActorScope
    ) -> bool:
        if item.resource_type == "organization":
            if actor_scope.organization_ids:
                return item.resource_id in actor_scope.organization_ids
            return False
        if item.resource_type == "team":
            if actor_scope.team_ids:
                return item.resource_id in actor_scope.team_ids
            return False
        if item.resource_type == "project":
            if actor_scope.project_ids:
                return item.resource_id in actor_scope.project_ids
            return False
        return True

    @staticmethod
    async def list_role_bindings_for_subject(
        db: AsyncSession,
        *,
        actor_scope: ActorScope,
        subject_type: str,
        subject_id: str,
    ) -> list[RoleBindingModel]:
        result = await db.execute(
            scoped_select(RoleBindingModel, actor_scope).where(
                RoleBindingModel.subject_type == subject_type,
                RoleBindingModel.subject_id == subject_id,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_role(
        db: AsyncSession, actor_scope: ActorScope, role_id: str
    ) -> RoleModel | None:
        result = await db.execute(
            scoped_select(RoleModel, actor_scope).where(RoleModel.id == role_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_roles(db: AsyncSession, actor_scope: ActorScope) -> list[RoleModel]:
        result = await db.execute(
            scoped_select(RoleModel, actor_scope).order_by(
                RoleModel.is_builtin.desc(),
                RoleModel.name.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_bindings(db: AsyncSession, actor_scope: ActorScope) -> list[RoleBindingModel]:
        result = await db.execute(
            scoped_select(RoleBindingModel, actor_scope).order_by(RoleBindingModel.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_binding(
        db: AsyncSession,
        actor_scope: ActorScope,
        binding_id: str,
    ) -> RoleBindingModel | None:
        result = await db.execute(
            scoped_select(RoleBindingModel, actor_scope).where(
                RoleBindingModel.id == binding_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def count_bindings_for_role(
        db: AsyncSession, actor_scope: ActorScope, role_id: str
    ) -> int:
        result = await db.execute(
            select(RoleBindingModel.id)
            .where(
                RoleBindingModel.tenant_id == actor_scope.tenant_id,
                RoleBindingModel.role_id == role_id,
            )
            .limit(1)
        )
        return 1 if result.first() is not None else 0
