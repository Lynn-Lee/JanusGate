"""#t63 RBAC 角色与角色绑定管理 API。

- 读接口要求 ``admin`` 或 ``rbac:read``；写接口要求 ``admin`` 或 ``rbac:admin``。
- 所有查询强制走 ``scoped_select`` 租户 scope helper（关闭 P2#6）。
- 跨租户访问一律 404，不泄露其它租户是否存在对应资源。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rbac_schemas import (
    RoleBindingCreate,
    RoleBindingListResponse,
    RoleBindingResponse,
    RoleCreate,
    RoleListResponse,
    RoleResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.rbac import ROLE_SCOPE_ORG, Role, RoleBinding
from app.models.tenancy import Organization
from app.services.rbac import BUILTIN_ROLES
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/rbac", tags=["RBAC"])


def _require(user: dict[str, Any], permission: str) -> None:
    """读写权限校验：``admin`` 始终放行，否则要求指定权限。"""

    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限: {permission}")


def _builtin_role_responses(tenant_id: str) -> list[RoleResponse]:
    return [
        RoleResponse(
            id=role.key,
            tenant_id=tenant_id,
            name=role.name,
            scope=role.scope,
            permissions=list(role.permissions),
            description=role.description,
            builtin=True,
        )
        for role in BUILTIN_ROLES.values()
    ]


@router.get("/roles", response_model=RoleListResponse)
async def list_roles(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleListResponse:
    _require(user, "rbac:read")
    tenant_id = str(user.get("tenant_id") or "default")

    items = _builtin_role_responses(tenant_id)
    result = await db.execute(scoped_select(Role, actor_scope_from_user(user)))
    for role in result.scalars().all():
        items.append(
            RoleResponse(
                id=role.id,
                tenant_id=role.tenant_id,
                name=role.name,
                scope=role.scope,
                permissions=_decode_permissions(role.permissions_json),
                description=role.description,
                builtin=False,
            )
        )
    return RoleListResponse(items=items, total=len(items))


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleResponse:
    _require(user, "rbac:admin")
    tenant_id = str(user.get("tenant_id") or "default")

    if data.name in {role.name for role in BUILTIN_ROLES.values()}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ROLE_NAME_RESERVED")

    role = Role(
        id=f"role_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        name=data.name,
        scope=data.scope,
        permissions_json=json.dumps(sorted(set(data.permissions))),
        description=data.description,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return RoleResponse(
        id=role.id,
        tenant_id=role.tenant_id,
        name=role.name,
        scope=role.scope,
        permissions=_decode_permissions(role.permissions_json),
        description=role.description,
        builtin=False,
    )


@router.get("/role-bindings", response_model=RoleBindingListResponse)
async def list_role_bindings(
    user_id: str | None = None,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleBindingListResponse:
    _require(user, "rbac:read")
    statement = scoped_select(RoleBinding, actor_scope_from_user(user))
    if user_id is not None:
        statement = statement.where(RoleBinding.user_id == user_id)
    result = await db.execute(statement)
    items = [_binding_response(binding) for binding in result.scalars().all()]
    return RoleBindingListResponse(items=items, total=len(items))


@router.post(
    "/role-bindings",
    response_model=RoleBindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_role_binding(
    data: RoleBindingCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> RoleBindingResponse:
    _require(user, "rbac:admin")
    tenant_id = str(user.get("tenant_id") or "default")

    await _validate_role_exists(db, tenant_id=tenant_id, role_id=data.role_id)

    organization_id = data.organization_id
    if data.scope_type == ROLE_SCOPE_ORG:
        if not organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="ORGANIZATION_REQUIRED"
            )
        organization = await db.get(Organization, organization_id)
        if organization is not None and organization.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="TENANT_SCOPE_VIOLATION"
            )
        if organization is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="ORGANIZATION_NOT_FOUND"
            )
    else:
        organization_id = ""

    binding = RoleBinding(
        id=f"rb_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        user_id=data.user_id,
        role_id=data.role_id,
        scope_type=data.scope_type,
        organization_id=organization_id,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return _binding_response(binding)


@router.delete("/role-bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role_binding(
    binding_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> None:
    _require(user, "rbac:admin")
    tenant_id = str(user.get("tenant_id") or "default")

    binding = await db.get(RoleBinding, binding_id)
    if binding is None or binding.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ROLE_BINDING_NOT_FOUND")
    await db.delete(binding)
    await db.commit()


async def _validate_role_exists(db: AsyncSession, *, tenant_id: str, role_id: str) -> None:
    if role_id in BUILTIN_ROLES:
        return
    role = await db.get(Role, role_id)
    if role is None or role.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ROLE_NOT_FOUND")


def _binding_response(binding: RoleBinding) -> RoleBindingResponse:
    return RoleBindingResponse(
        id=binding.id,
        tenant_id=binding.tenant_id,
        user_id=binding.user_id,
        role_id=binding.role_id,
        scope_type=binding.scope_type,
        organization_id=binding.organization_id,
    )


def _decode_permissions(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]
