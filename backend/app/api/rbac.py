"""#t63 RBAC 角色、绑定与有效权限 API。"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rbac_schemas import (
    EffectiveRbacResponse,
    ObjectPermissionResponse,
    RoleBindingCreate,
    RoleBindingListResponse,
    RoleBindingResponse,
    RoleCreate,
    RoleListResponse,
    RoleResponse,
    RoleUpdate,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.rbac import (
    SCOPE_ORGANIZATION,
    RoleBindingModel,
    RoleModel,
    RoleObjectPermissionModel,
)
from app.rbac.ops import dump_json_list, new_binding_id, new_object_permission_id, new_role_id
from app.rbac.repository import (
    ensure_builtin_roles,
    role_menu_permissions,
    role_permissions,
    validate_scope,
)
from app.rbac.resolver import RbacResolver
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(tags=["RBAC"])


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
        display_name=role.display_name,
        scope_type=role.scope_type,  # type: ignore[arg-type]
        organization_id=role.organization_id,
        is_builtin=role.is_builtin,
        builtin_key=role.builtin_key,
        description=role.description,
        permissions=role_permissions(role),
        menu_permissions=role_menu_permissions(role),
    )


def _binding_response(binding: RoleBindingModel) -> RoleBindingResponse:
    return RoleBindingResponse(
        id=binding.id,
        tenant_id=binding.tenant_id,
        role_id=binding.role_id,
        subject_type=binding.subject_type,  # type: ignore[arg-type]
        subject_id=binding.subject_id,
        scope_type=binding.scope_type,  # type: ignore[arg-type]
        organization_id=binding.organization_id,
    )


@router.get("/rbac/effective", response_model=EffectiveRbacResponse)
async def get_effective_rbac(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> EffectiveRbacResponse:
    actor_scope = actor_scope_from_user(user)
    effective = await RbacResolver.resolve(
        db,
        actor_scope=actor_scope,
        group_ids=tuple(str(g) for g in user.get("group_ids", ())),
        is_superuser=False,
    )
    return EffectiveRbacResponse(
        permissions=list(effective.permissions),
        menu_permissions=list(effective.menu_permissions),
        role_ids=list(effective.role_ids),
        object_permissions=[
            ObjectPermissionResponse(
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                action=item.action,
            )
            for item in effective.object_permissions
        ],
    )


@router.get("/roles/", response_model=RoleListResponse)
async def list_roles(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleListResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    await ensure_builtin_roles(db, actor_scope.tenant_id)
    roles = await RbacResolver.list_roles(db, actor_scope)
    items = [_role_response(role) for role in roles]
    return RoleListResponse(items=items, total=len(items))


@router.post("/roles/", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleResponse:
    _require_rbac_permission(user, "rbac:manage")
    actor_scope = actor_scope_from_user(user)
    try:
        validate_scope(scope_type=data.scope_type, organization_id=data.organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = await db.execute(
        scoped_select(RoleModel, actor_scope).where(
            RoleModel.name == data.name,
            RoleModel.scope_type == data.scope_type,
            RoleModel.organization_id == data.organization_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="ROLE_ALREADY_EXISTS")

    role = RoleModel(
        id=new_role_id(),
        tenant_id=actor_scope.tenant_id,
        name=data.name,
        display_name=data.display_name,
        scope_type=data.scope_type,
        organization_id=data.organization_id,
        is_builtin=False,
        builtin_key=None,
        description=data.description,
        permissions_json=dump_json_list(data.permissions),
        menu_permissions_json=dump_json_list(data.menu_permissions),
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return _role_response(role)


@router.get("/roles/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    role = await RbacResolver.get_role(db, actor_scope, role_id)
    if role is None:
        _not_found("ROLE_NOT_FOUND")
    return _role_response(role)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleResponse:
    _require_rbac_permission(user, "rbac:manage")
    actor_scope = actor_scope_from_user(user)
    role = await RbacResolver.get_role(db, actor_scope, role_id)
    if role is None:
        _not_found("ROLE_NOT_FOUND")
    if role.is_builtin:
        raise HTTPException(status_code=400, detail="BUILTIN_ROLE_IMMUTABLE")

    if data.display_name is not None:
        role.display_name = data.display_name
    if data.description is not None:
        role.description = data.description
    if data.permissions is not None:
        role.permissions_json = dump_json_list(data.permissions)
    if data.menu_permissions is not None:
        role.menu_permissions_json = dump_json_list(data.menu_permissions)

    await db.commit()
    await db.refresh(role)
    return _role_response(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_role(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_rbac_permission(user, "rbac:manage")
    actor_scope = actor_scope_from_user(user)
    role = await RbacResolver.get_role(db, actor_scope, role_id)
    if role is None:
        _not_found("ROLE_NOT_FOUND")
    if role.is_builtin:
        raise HTTPException(status_code=400, detail="BUILTIN_ROLE_IMMUTABLE")

    binding_count = await RbacResolver.count_bindings_for_role(db, actor_scope, role_id)
    if binding_count:
        raise HTTPException(status_code=400, detail="ROLE_HAS_BINDINGS")

    object_perms = await db.execute(
        scoped_select(RoleObjectPermissionModel, actor_scope).where(
            RoleObjectPermissionModel.role_id == role_id
        )
    )
    for item in object_perms.scalars().all():
        await db.delete(item)
    await db.delete(role)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/role-bindings/", response_model=RoleBindingListResponse)
async def list_role_bindings(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleBindingListResponse:
    _require_rbac_permission(user, "rbac:read")
    actor_scope = actor_scope_from_user(user)
    bindings = await RbacResolver.list_bindings(db, actor_scope)
    items = [_binding_response(binding) for binding in bindings]
    return RoleBindingListResponse(items=items, total=len(items))


@router.post(
    "/role-bindings/",
    response_model=RoleBindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_role_binding(
    data: RoleBindingCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleBindingResponse:
    _require_rbac_permission(user, "rbac:manage")
    actor_scope = actor_scope_from_user(user)
    try:
        validate_scope(scope_type=data.scope_type, organization_id=data.organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    role = await RbacResolver.get_role(db, actor_scope, data.role_id)
    if role is None:
        _not_found("ROLE_NOT_FOUND")
    if (
        role.scope_type == SCOPE_ORGANIZATION
        and data.scope_type == SCOPE_ORGANIZATION
        and role.organization_id
        and role.organization_id != data.organization_id
    ):
        raise HTTPException(status_code=400, detail="ROLE_ORG_SCOPE_MISMATCH")

    existing = await db.execute(
        scoped_select(RoleBindingModel, actor_scope).where(
            RoleBindingModel.role_id == data.role_id,
            RoleBindingModel.subject_type == data.subject_type,
            RoleBindingModel.subject_id == data.subject_id,
            RoleBindingModel.scope_type == data.scope_type,
            RoleBindingModel.organization_id == data.organization_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="BINDING_ALREADY_EXISTS")

    binding = RoleBindingModel(
        id=new_binding_id(),
        tenant_id=actor_scope.tenant_id,
        role_id=data.role_id,
        subject_type=data.subject_type,
        subject_id=data.subject_id,
        scope_type=data.scope_type,
        organization_id=data.organization_id,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return _binding_response(binding)


@router.delete(
    "/role-bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_role_binding(
    binding_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_rbac_permission(user, "rbac:manage")
    actor_scope = actor_scope_from_user(user)
    binding = await RbacResolver.find_binding(db, actor_scope, binding_id)
    if binding is None:
        _not_found("BINDING_NOT_FOUND")
    await db.delete(binding)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/roles/{role_id}/object-permissions",
    response_model=ObjectPermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_object_permission(
    role_id: str,
    payload: ObjectPermissionResponse,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ObjectPermissionResponse:
    _require_rbac_permission(user, "rbac:manage")
    actor_scope = actor_scope_from_user(user)
    role = await RbacResolver.get_role(db, actor_scope, role_id)
    if role is None:
        _not_found("ROLE_NOT_FOUND")
    if role.is_builtin:
        raise HTTPException(status_code=400, detail="BUILTIN_ROLE_IMMUTABLE")

    existing = await db.execute(
        scoped_select(RoleObjectPermissionModel, actor_scope).where(
            RoleObjectPermissionModel.role_id == role_id,
            RoleObjectPermissionModel.resource_type == payload.resource_type,
            RoleObjectPermissionModel.resource_id == payload.resource_id,
            RoleObjectPermissionModel.action == payload.action,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="OBJECT_PERMISSION_ALREADY_EXISTS")

    item = RoleObjectPermissionModel(
        id=new_object_permission_id(),
        tenant_id=actor_scope.tenant_id,
        role_id=role_id,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        action=payload.action,
    )
    db.add(item)
    await db.commit()
    return payload
