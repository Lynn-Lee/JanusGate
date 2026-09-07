"""#t73 账号模板 CRUD（系统设置风格，权限 accounts:read|write）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account_template_schemas import (
    AccountTemplateCreate,
    AccountTemplateListResponse,
    AccountTemplateResponse,
    AccountTemplateUpdate,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.account import Account, AccountTemplate
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(prefix="/account-templates", tags=["账号模板"])

FIXED_PROTOCOL = "ssh"


def _require_account_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _require_template_list_permission(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    allowed = {"admin", "accounts:read", "accounts:write"}
    if allowed.intersection(permissions):
        return
    raise HTTPException(status_code=403, detail="缺少权限: accounts:read")


@router.get("/", response_model=AccountTemplateListResponse)
async def list_account_templates(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountTemplateListResponse:
    _require_template_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(AccountTemplate, actor_scope).order_by(AccountTemplate.id.asc())
    )
    templates = list(result.scalars().all())
    items = [_template_response(row) for row in templates]
    return AccountTemplateListResponse(items=items, total=len(items))


@router.post("/", response_model=AccountTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_account_template(
    data: AccountTemplateCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountTemplateResponse:
    _require_account_permission(user, "accounts:write")
    actor_scope = actor_scope_from_user(user)
    name = data.name.strip()
    default_username = data.default_username.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if not default_username:
        raise HTTPException(status_code=400, detail="默认用户名不能为空")
    template = AccountTemplate(
        tenant_id=actor_scope.tenant_id,
        name=name,
        protocol=FIXED_PROTOCOL,
        default_username=default_username,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_response(template)


@router.get("/{template_id}", response_model=AccountTemplateResponse)
async def get_account_template(
    template_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountTemplateResponse:
    _require_template_list_permission(user)
    actor_scope = actor_scope_from_user(user)
    template = await _get_scoped_template(db, actor_scope, template_id)
    return _template_response(template)


@router.patch("/{template_id}", response_model=AccountTemplateResponse)
async def update_account_template(
    template_id: int,
    data: AccountTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountTemplateResponse:
    _require_account_permission(user, "accounts:write")
    actor_scope = actor_scope_from_user(user)
    template = await _get_scoped_template(db, actor_scope, template_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        template.name = name
    if "default_username" in payload:
        default_username = str(payload["default_username"] or "").strip()
        if not default_username:
            raise HTTPException(status_code=400, detail="默认用户名不能为空")
        template.default_username = default_username
    # protocol 固定 ssh，忽略客户端任何协议字段
    template.protocol = FIXED_PROTOCOL
    await db.commit()
    await db.refresh(template)
    return _template_response(template)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_account_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    """删除模板；账号 template_id 由 FK ON DELETE SET NULL 仅 unlink。"""

    _require_account_permission(user, "accounts:write")
    actor_scope = actor_scope_from_user(user)
    template = await _get_scoped_template(db, actor_scope, template_id)
    await db.execute(
        update(Account)
        .where(Account.tenant_id == actor_scope.tenant_id)
        .where(Account.template_id == template.id)
        .values(template_id=None)
    )
    await db.delete(template)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_scoped_template(
    db: AsyncSession, actor_scope: ActorScope, template_id: int
) -> AccountTemplate:
    result = await db.execute(
        scoped_select(AccountTemplate, actor_scope).where(AccountTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="ACCOUNT_TEMPLATE_NOT_FOUND")
    return template


def _template_response(template: AccountTemplate) -> AccountTemplateResponse:
    return AccountTemplateResponse(
        id=template.id,
        name=template.name,
        protocol=FIXED_PROTOCOL,
        default_username=template.default_username,
    )
