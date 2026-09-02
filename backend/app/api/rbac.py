"""#t63 RBAC 管理 API：角色、绑定、用户组与对象级权限。"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rbac_schemas import (
    EffectiveRbacResponse,
    ObjectPermissionCreate,
    ObjectPermissionListResponse,
    ObjectPermissionResponse,
    RoleBindingCreate,
    RoleBindingListResponse,
    RoleBindingResponse,
    RoleCreate,
    RoleListResponse,
    RoleResponse,
    UserGroupCreate,
    UserGroupListResponse,
    UserGroupMembersUpdate,
    UserGroupResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.rbac import (
    ObjectPermissionModel,
    RbacSubjectType,
    RoleBindingModel,
    RoleModel,
    RoleScope,
    UserGroupModel,
)
from app.policy.rbac import (
    RbacService,
    dump_json_list,
    new_rbac_id,
    parse_json_list,
)
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(prefix="/rbac", tags=["RBAC"])


def _require_rbac_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _not_found(code: str) -> NoReturn:
    raise HTTPException(status_code=404, detail=code)


def _role_response(role: RoleModel) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        tenant_id=role.tenant_id,
        name=role.name,
        scope=role.scope.value if isinstance(role.scope, RoleScope) else str(role.scope),
        organization_id=role.organization_id,
        builtin_key=role.builtin_key,
        permissions=parse_json_list(role.permissions_json),
        menu_permissions=parse_json_list(role.menu_permissions_json),
        is_active=role.is_active,
    )


def _binding_response(binding: RoleBindingModel) -> RoleBindingResponse:
    subject_type = (
        binding.subject_type.value
        if isinstance(binding.subject_type, RbacSubjectType)
        else str(binding.subject_type)
    )
    return RoleBindingResponse(
        id=binding.id,
        tenant_id=binding.tenant_id,
        role_id=binding.role_id,
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=binding.subject_id,
        organization_id=binding.organization_id,
        is_active=binding.is_active,
    )


def _group_response(group: UserGroupModel) -> UserGroupResponse:
    return UserGroupResponse(
        id=group.id,
        tenant_id=group.tenant_id,
        name=group.name,
        member_ids=parse_json_list(group.member_ids_json),
        is_active=group.is_active,
    )


def _object_permission_response(permission: ObjectPermissionModel) -> ObjectPermissionResponse:
    subject_type = (
        permission.subject_type.value
        if isinstance(permission.subject_type, RbacSubjectType)
        else str(permission.subject_type)
    )
    return ObjectPermissionResponse(
        id=permission.id,
        tenant_id=permission.tenant_id,
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=permission.subject_id,
        resource_type=permission.resource_type,
        resource_id=permission.resource_id,
        action=permission.action,
        organization_id=permission.organization_id,
        is_active=permission.is_active,
    )


async def _get_role(db: AsyncSession, actor_scope: ActorScope, role_id: str) -> RoleModel:
    result = await db.execute(
        scoped_select(RoleModel, actor_scope).where(RoleModel.id == role_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        _not_found("ROLE_NOT_FOUND")
    return role


@router.get("/roles", response_model=RoleListResponse)
async def list_roles(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleListResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    await RbacService.ensure_builtin_roles(db, actor_scope.tenant_id)
    result = await db.execute(scoped_select(RoleModel, actor_scope))
    items = [_role_response(role) for role in result.scalars()]
    return RoleListResponse(items=items, total=len(items))


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    data: RoleCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleResponse:
    _require_rbac_permission(user, "rbac:write")
    actor_scope = actor_scope_from_user(user)
    role = RoleModel(
        id=new_rbac_id("role"),
        tenant_id=actor_scope.tenant_id,
        name=data.name,
        scope=RoleScope(data.scope),
        organization_id=data.organization_id,
        permissions_json=dump_json_list(data.permissions),
        menu_permissions_json=dump_json_list(data.menu_permissions),
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return _role_response(role)


@router.get("/role-bindings", response_model=RoleBindingListResponse)
async def list_role_bindings(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> RoleBindingListResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(RoleBindingModel, actor_scope))
    items = [_binding_response(binding) for binding in result.scalars()]
    return RoleBindingListResponse(items=items, total=len(items))


@router.post(
    "/role-bindings",
    response_model=RoleBindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_role_binding(
    data: RoleBindingCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleBindingResponse:
    _require_rbac_permission(user, "rbac:write")
    actor_scope = actor_scope_from_user(user)
    await _get_role(db, actor_scope, data.role_id)
    if data.subject_type == "user_group":
        group = await _get_user_group(db, actor_scope, data.subject_id)
        if group is None:
            _not_found("USER_GROUP_NOT_FOUND")
    binding = RoleBindingModel(
        id=new_rbac_id("binding"),
        tenant_id=actor_scope.tenant_id,
        role_id=data.role_id,
        subject_type=RbacSubjectType(data.subject_type),
        subject_id=data.subject_id,
        organization_id=data.organization_id,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return _binding_response(binding)


@router.get("/user-groups", response_model=UserGroupListResponse)
async def list_user_groups(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> UserGroupListResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(UserGroupModel, actor_scope))
    items = [_group_response(group) for group in result.scalars()]
    return UserGroupListResponse(items=items, total=len(items))


@router.post("/user-groups", response_model=UserGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_user_group(
    data: UserGroupCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UserGroupResponse:
    _require_rbac_permission(user, "rbac:write")
    actor_scope = actor_scope_from_user(user)
    group = UserGroupModel(
        id=new_rbac_id("group"),
        tenant_id=actor_scope.tenant_id,
        name=data.name,
        member_ids_json=dump_json_list(data.member_ids),
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return _group_response(group)


@router.patch("/user-groups/{group_id}/members", response_model=UserGroupResponse)
async def update_user_group_members(
    group_id: str,
    data: UserGroupMembersUpdate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UserGroupResponse:
    _require_rbac_permission(user, "rbac:write")
    actor_scope = actor_scope_from_user(user)
    group = await _get_user_group(db, actor_scope, group_id)
    if group is None:
        _not_found("USER_GROUP_NOT_FOUND")
    group.member_ids_json = dump_json_list(data.member_ids)
    await db.commit()
    await db.refresh(group)
    return _group_response(group)


@router.get("/object-permissions", response_model=ObjectPermissionListResponse)
async def list_object_permissions(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_read_db),
) -> ObjectPermissionListResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(ObjectPermissionModel, actor_scope))
    items = [_object_permission_response(item) for item in result.scalars()]
    return ObjectPermissionListResponse(items=items, total=len(items))


@router.post(
    "/object-permissions",
    response_model=ObjectPermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_object_permission(
    data: ObjectPermissionCreate,
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ObjectPermissionResponse:
    _require_rbac_permission(user, "rbac:write")
    actor_scope = actor_scope_from_user(user)
    if data.subject_type == "user_group":
        group = await _get_user_group(db, actor_scope, data.subject_id)
        if group is None:
            _not_found("USER_GROUP_NOT_FOUND")
    permission = ObjectPermissionModel(
        id=new_rbac_id("objperm"),
        tenant_id=actor_scope.tenant_id,
        subject_type=RbacSubjectType(data.subject_type),
        subject_id=data.subject_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        action=data.action,
        organization_id=data.organization_id,
    )
    db.add(permission)
    await db.commit()
    await db.refresh(permission)
    return _object_permission_response(permission)


@router.get("/me/effective", response_model=EffectiveRbacResponse)
async def get_effective_rbac(
    user: dict[str, Any] = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> EffectiveRbacResponse:
    effective = await RbacService.resolve_effective_rbac(
        db,
        user_id=str(user["id"]),
        tenant_id=str(user.get("tenant_id") or "default"),
        is_superuser=False,
        organization_id=user.get("organization_id"),
    )
    return EffectiveRbacResponse(
        permissions=list(effective.permissions),
        menu_permissions=list(effective.menu_permissions),
        group_ids=list(effective.group_ids),
        role_ids=list(effective.role_ids),
    )


async def _get_user_group(
    db: AsyncSession, actor_scope: ActorScope, group_id: str
) -> UserGroupModel | None:
    result = await db.execute(
        scoped_select(UserGroupModel, actor_scope).where(UserGroupModel.id == group_id)
    )
    return result.scalar_one_or_none()
