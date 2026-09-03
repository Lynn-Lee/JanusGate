"""#t63 RBAC 持久化与内置角色种子。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import (
    SCOPE_ORGANIZATION,
    SCOPE_SYSTEM,
    RoleBindingModel,
    RoleModel,
    RoleObjectPermissionModel,
)
from app.rbac.constants import BUILTIN_ROLE_DEFINITIONS, BUILTIN_USER
from app.rbac.ops import dump_json_list, load_json_list, new_binding_id, new_role_id
from app.tenancy.scope import ActorScope, scoped_select


async def ensure_builtin_roles(db: AsyncSession, tenant_id: str) -> list[RoleModel]:
    """为租户补齐四个内置角色，幂等。"""
    result = await db.execute(select(RoleModel).where(RoleModel.tenant_id == tenant_id))
    existing = {role.builtin_key: role for role in result.scalars().all() if role.builtin_key}
    created: list[RoleModel] = []
    for builtin_key, definition in BUILTIN_ROLE_DEFINITIONS.items():
        if builtin_key in existing:
            continue
        role = RoleModel(
            id=new_role_id(),
            tenant_id=tenant_id,
            name=str(definition["name"]),
            display_name=str(definition["display_name"]),
            scope_type=str(definition["scope_type"]),
            organization_id=(
                str(definition["organization_id"])
                if definition.get("organization_id") is not None
                else None
            ),
            is_builtin=True,
            builtin_key=builtin_key,
            description=str(definition["description"]),
            permissions_json=dump_json_list(
                [str(p) for p in _as_str_sequence(definition["permissions"])]
            ),
            menu_permissions_json=dump_json_list(
                [str(m) for m in _as_str_sequence(definition["menus"])]
            ),
        )
        db.add(role)
        created.append(role)
    if created:
        await db.commit()
        for role in created:
            await db.refresh(role)
    return created


async def list_object_permissions_for_roles(
    db: AsyncSession,
    actor_scope: ActorScope,
    role_ids: tuple[str, ...],
) -> list[RoleObjectPermissionModel]:
    if not role_ids:
        return []
    result = await db.execute(
        scoped_select(RoleObjectPermissionModel, actor_scope).where(
            RoleObjectPermissionModel.role_id.in_(role_ids)
        )
    )
    return list(result.scalars().all())


async def ensure_default_user_binding(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
) -> RoleBindingModel | None:
    """无绑定时为用户挂上内置普通用户角色。"""
    actor_scope = ActorScope(user_id=user_id, tenant_id=tenant_id)
    existing = await db.execute(
        scoped_select(RoleBindingModel, actor_scope).where(
            RoleBindingModel.subject_type == "user",
            RoleBindingModel.subject_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    await ensure_builtin_roles(db, tenant_id)
    role_result = await db.execute(
        select(RoleModel).where(
            RoleModel.tenant_id == tenant_id,
            RoleModel.builtin_key == BUILTIN_USER,
        )
    )
    role = role_result.scalar_one_or_none()
    if role is None:
        return None

    binding = RoleBindingModel(
        id=new_binding_id(),
        tenant_id=tenant_id,
        role_id=role.id,
        subject_type="user",
        subject_id=user_id,
        scope_type=SCOPE_SYSTEM,
        organization_id=None,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return binding


async def ensure_superuser_binding(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
) -> RoleBindingModel | None:
    """超级用户自动绑定系统管理员角色，承接 is_superuser 迁移路径。"""
    await ensure_builtin_roles(db, tenant_id)
    role_result = await db.execute(
        select(RoleModel).where(
            RoleModel.tenant_id == tenant_id,
            RoleModel.builtin_key == "system_admin",
        )
    )
    role = role_result.scalar_one_or_none()
    if role is None:
        return None

    actor_scope = ActorScope(user_id=user_id, tenant_id=tenant_id, permissions=("admin",))
    existing = await db.execute(
        scoped_select(RoleBindingModel, actor_scope).where(
            RoleBindingModel.subject_type == "user",
            RoleBindingModel.subject_id == user_id,
            RoleBindingModel.role_id == role.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    binding = RoleBindingModel(
        id=new_binding_id(),
        tenant_id=tenant_id,
        role_id=role.id,
        subject_type="user",
        subject_id=user_id,
        scope_type=SCOPE_SYSTEM,
        organization_id=None,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return binding


def role_permissions(role: RoleModel) -> list[str]:
    return load_json_list(role.permissions_json)


def role_menu_permissions(role: RoleModel) -> list[str]:
    return load_json_list(role.menu_permissions_json)


def _as_str_sequence(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    raise TypeError("EXPECTED_STR_SEQUENCE")


def validate_scope(
    *,
    scope_type: str,
    organization_id: str | None,
) -> None:
    if scope_type not in {SCOPE_SYSTEM, SCOPE_ORGANIZATION}:
        raise ValueError("SCOPE_TYPE_INVALID")
    if scope_type == SCOPE_ORGANIZATION and not organization_id:
        raise ValueError("ORGANIZATION_ID_REQUIRED")
    if scope_type == SCOPE_SYSTEM and organization_id:
        raise ValueError("ORGANIZATION_ID_NOT_ALLOWED")
