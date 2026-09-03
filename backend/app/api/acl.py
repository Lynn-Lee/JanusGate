"""#t65 命令过滤 ACL 与数据脱敏规则的租户隔离管理 API。

本模块只覆盖 roadmap 已落地的两类模型：``CommandFilterAclModel`` 与
``DataMaskingRuleModel``（命令组作为 ACL 的内嵌写入面一并持久化）。登录 ACL 不在本切片。

所有查询强制走 :func:`~app.tenancy.scope.scoped_select`，跨租户一律 404 fail-closed，
不泄露资源是否存在。
"""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime, time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.acl_schemas import (
    OVERLAY_PROTOCOLS,
    OVERLAY_RESOURCE_TYPES,
    CommandFilterAclCreate,
    CommandFilterAclListResponse,
    CommandFilterAclResponse,
    CommandFilterAclUpdate,
    CommandGroupPayload,
    CommandGroupResponse,
    ConnectMethodAclCreate,
    ConnectMethodAclListResponse,
    ConnectMethodAclResponse,
    ConnectMethodAclUpdate,
    DataMaskingRuleCreate,
    DataMaskingRuleListResponse,
    DataMaskingRuleResponse,
    DataMaskingRuleUpdate,
    LoginAclCreate,
    LoginAclListResponse,
    LoginAclResponse,
    LoginAclUpdate,
    LoginAssetAclCreate,
    LoginAssetAclListResponse,
    LoginAssetAclResponse,
    LoginAssetAclUpdate,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.acl import (
    CommandFilterAclModel,
    CommandGroupModel,
    ConnectMethodAclModel,
    DataMaskingRuleModel,
    LoginAclModel,
    LoginAssetAclModel,
)
from app.models.user import User
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(tags=["ACL"])


@router.get("/command-filter-acls/", response_model=CommandFilterAclListResponse)
async def list_command_filter_acls(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> CommandFilterAclListResponse:
    _require_acl_permission(user, "acl:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(CommandFilterAclModel, actor_scope).order_by(
            CommandFilterAclModel.priority.asc(), CommandFilterAclModel.id.asc()
        )
    )
    acls = list(result.scalars().all())
    groups_by_id = await _load_groups_by_id(db, actor_scope)
    items = [_acl_response(acl, groups_by_id) for acl in acls]
    return CommandFilterAclListResponse(items=items, total=len(items))


@router.post(
    "/command-filter-acls/",
    response_model=CommandFilterAclResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_command_filter_acl(
    data: CommandFilterAclCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> CommandFilterAclResponse:
    _require_acl_permission(user, "acl:write")
    tenant_id = str(user.get("tenant_id") or "default")
    groups = _new_command_groups(tenant_id, data.command_groups)
    acl = CommandFilterAclModel(
        id=f"cfa_{uuid4().hex}",
        tenant_id=tenant_id,
        name=data.name,
        priority=data.priority,
        action=data.action,
        reviewer_subject_ids_json=_dump_ids(data.reviewer_subject_ids),
        subject_ids_json=_dump_ids(data.subject_ids),
        asset_ids_json=_dump_ids(data.asset_ids),
        account_ids_json=_dump_ids(data.account_ids),
        command_group_ids_json=_dump_ids([group.id for group in groups]),
        is_active=data.is_active,
    )
    db.add_all([*groups, acl])
    await db.commit()
    await db.refresh(acl)
    return _acl_response(acl, {group.id: group for group in groups})


@router.get("/command-filter-acls/{acl_id}", response_model=CommandFilterAclResponse)
async def get_command_filter_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> CommandFilterAclResponse:
    _require_acl_permission(user, "acl:read")
    actor_scope = actor_scope_from_user(user)
    acl = await _get_scoped_acl(db, actor_scope, acl_id)
    groups_by_id = await _load_groups_by_id(db, actor_scope)
    return _acl_response(acl, groups_by_id)


@router.patch("/command-filter-acls/{acl_id}", response_model=CommandFilterAclResponse)
async def update_command_filter_acl(
    acl_id: str,
    data: CommandFilterAclUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> CommandFilterAclResponse:
    _require_acl_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    acl = await _get_scoped_acl(db, actor_scope, acl_id)
    if data.name is not None:
        acl.name = data.name
    if data.priority is not None:
        acl.priority = data.priority
    if data.action is not None:
        acl.action = data.action
    if data.reviewer_subject_ids is not None:
        acl.reviewer_subject_ids_json = _dump_ids(data.reviewer_subject_ids)
    if data.subject_ids is not None:
        acl.subject_ids_json = _dump_ids(data.subject_ids)
    if data.asset_ids is not None:
        acl.asset_ids_json = _dump_ids(data.asset_ids)
    if data.account_ids is not None:
        acl.account_ids_json = _dump_ids(data.account_ids)
    if data.is_active is not None:
        acl.is_active = data.is_active
    groups_by_id = await _load_groups_by_id(db, actor_scope)
    if data.command_groups is not None:
        groups = _new_command_groups(acl.tenant_id, data.command_groups)
        db.add_all(groups)
        acl.command_group_ids_json = _dump_ids([group.id for group in groups])
        groups_by_id.update({group.id: group for group in groups})
    await db.commit()
    await db.refresh(acl)
    return _acl_response(acl, groups_by_id)


@router.delete("/command-filter-acls/{acl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_command_filter_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_acl_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    acl = await _get_scoped_acl(db, actor_scope, acl_id)
    await db.delete(acl)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/data-masking-rules/", response_model=DataMaskingRuleListResponse)
async def list_data_masking_rules(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> DataMaskingRuleListResponse:
    _require_acl_permission(user, "acl:read")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(DataMaskingRuleModel, actor_scope).order_by(
            DataMaskingRuleModel.priority.asc(), DataMaskingRuleModel.id.asc()
        )
    )
    rules = list(result.scalars().all())
    items = [_masking_response(rule) for rule in rules]
    return DataMaskingRuleListResponse(items=items, total=len(items))


@router.post(
    "/data-masking-rules/",
    response_model=DataMaskingRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_data_masking_rule(
    data: DataMaskingRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> DataMaskingRuleResponse:
    _require_acl_permission(user, "acl:write")
    rule = DataMaskingRuleModel(
        id=f"dmr_{uuid4().hex}",
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        priority=data.priority,
        match_type=data.match_type,
        patterns_json=_dump_ids(data.patterns),
        mask_method=data.mask_method,
        keep_prefix=data.keep_prefix,
        keep_suffix=data.keep_suffix,
        placeholder=data.placeholder,
        subject_ids_json=_dump_ids(data.subject_ids),
        asset_ids_json=_dump_ids(data.asset_ids),
        account_ids_json=_dump_ids(data.account_ids),
        is_active=data.is_active,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _masking_response(rule)


@router.get("/data-masking-rules/{rule_id}", response_model=DataMaskingRuleResponse)
async def get_data_masking_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> DataMaskingRuleResponse:
    _require_acl_permission(user, "acl:read")
    rule = await _get_scoped_masking_rule(db, actor_scope_from_user(user), rule_id)
    return _masking_response(rule)


@router.patch("/data-masking-rules/{rule_id}", response_model=DataMaskingRuleResponse)
async def update_data_masking_rule(
    rule_id: str,
    data: DataMaskingRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> DataMaskingRuleResponse:
    _require_acl_permission(user, "acl:write")
    rule = await _get_scoped_masking_rule(db, actor_scope_from_user(user), rule_id)
    if data.name is not None:
        rule.name = data.name
    if data.priority is not None:
        rule.priority = data.priority
    if data.match_type is not None:
        rule.match_type = data.match_type
    if data.patterns is not None:
        rule.patterns_json = _dump_ids(data.patterns)
    if data.mask_method is not None:
        rule.mask_method = data.mask_method
    if data.keep_prefix is not None:
        rule.keep_prefix = data.keep_prefix
    if data.keep_suffix is not None:
        rule.keep_suffix = data.keep_suffix
    if data.placeholder is not None:
        rule.placeholder = data.placeholder
    if data.subject_ids is not None:
        rule.subject_ids_json = _dump_ids(data.subject_ids)
    if data.asset_ids is not None:
        rule.asset_ids_json = _dump_ids(data.asset_ids)
    if data.account_ids is not None:
        rule.account_ids_json = _dump_ids(data.account_ids)
    if data.is_active is not None:
        rule.is_active = data.is_active
    await db.commit()
    await db.refresh(rule)
    return _masking_response(rule)


@router.delete("/data-masking-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_masking_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_acl_permission(user, "acl:write")
    rule = await _get_scoped_masking_rule(db, actor_scope_from_user(user), rule_id)
    await db.delete(rule)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)



@router.get("/login-acls/", response_model=LoginAclListResponse)
async def list_login_acls(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAclListResponse:
    _require_acl_permission(user, "acl:read")
    result = await db.execute(
        scoped_select(LoginAclModel, actor_scope_from_user(user)).order_by(
            LoginAclModel.priority.asc(), LoginAclModel.id.asc()
        )
    )
    items = [await _login_acl_response(db, acl) for acl in result.scalars().all()]
    return LoginAclListResponse(items=items, total=len(items))


@router.post("/login-acls/", response_model=LoginAclResponse, status_code=status.HTTP_201_CREATED)
async def create_login_acl(
    data: LoginAclCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAclResponse:
    _require_acl_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    await _require_existing_login_subject(db, actor_scope, data.subject_id)
    acl = LoginAclModel(
        id=f"la_{uuid4().hex}",
        tenant_id=str(user.get("tenant_id") or "default"),
        name=_overlay_name(data.name, data.subject_id),
        priority=data.priority,
        action=data.action,
        subject_id=data.subject_id,
    )
    db.add(acl)
    await db.commit()
    await db.refresh(acl)
    return await _login_acl_response(db, acl)


@router.get("/login-acls/{acl_id}", response_model=LoginAclResponse)
async def get_login_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAclResponse:
    _require_acl_permission(user, "acl:read")
    acl = await _get_scoped_login_acl(db, actor_scope_from_user(user), acl_id)
    return await _login_acl_response(db, acl)


@router.patch("/login-acls/{acl_id}", response_model=LoginAclResponse)
async def update_login_acl(
    acl_id: str,
    data: LoginAclUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAclResponse:
    _require_acl_permission(user, "acl:write")
    actor_scope = actor_scope_from_user(user)
    acl = await _get_scoped_login_acl(db, actor_scope, acl_id)
    if data.name is not None:
        acl.name = data.name
    if data.priority is not None:
        acl.priority = data.priority
    if data.action is not None:
        acl.action = data.action
    if data.subject_id is not None:
        await _require_existing_login_subject(db, actor_scope, data.subject_id)
        acl.subject_id = data.subject_id
    await db.commit()
    await db.refresh(acl)
    return await _login_acl_response(db, acl)


@router.delete("/login-acls/{acl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_login_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_acl_permission(user, "acl:write")
    acl = await _get_scoped_login_acl(db, actor_scope_from_user(user), acl_id)
    await db.delete(acl)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/login-asset-acls/", response_model=LoginAssetAclListResponse)
async def list_login_asset_acls(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAssetAclListResponse:
    _require_acl_permission(user, "acl:read")
    result = await db.execute(
        scoped_select(LoginAssetAclModel, actor_scope_from_user(user)).order_by(
            LoginAssetAclModel.priority.asc(), LoginAssetAclModel.id.asc()
        )
    )
    items = [_login_asset_acl_response(acl) for acl in result.scalars().all()]
    return LoginAssetAclListResponse(items=items, total=len(items))


@router.post(
    "/login-asset-acls/",
    response_model=LoginAssetAclResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_login_asset_acl(
    data: LoginAssetAclCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAssetAclResponse:
    _require_acl_permission(user, "acl:write")
    _validate_login_asset_fields(
        resource_type=data.resource_type,
        ip_cidr=data.ip_cidr,
        time_start=data.time_start,
        time_end=data.time_end,
    )
    acl = LoginAssetAclModel(
        id=f"laa_{uuid4().hex}",
        tenant_id=str(user.get("tenant_id") or "default"),
        name=_overlay_name(data.name, data.resource_id),
        priority=data.priority,
        action=data.action,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        ip_cidr=_empty_to_none(data.ip_cidr),
        time_start=_empty_to_none(data.time_start),
        time_end=_empty_to_none(data.time_end),
    )
    db.add(acl)
    await db.commit()
    await db.refresh(acl)
    return _login_asset_acl_response(acl)


@router.get("/login-asset-acls/{acl_id}", response_model=LoginAssetAclResponse)
async def get_login_asset_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAssetAclResponse:
    _require_acl_permission(user, "acl:read")
    return _login_asset_acl_response(
        await _get_scoped_login_asset_acl(db, actor_scope_from_user(user), acl_id)
    )


@router.patch("/login-asset-acls/{acl_id}", response_model=LoginAssetAclResponse)
async def update_login_asset_acl(
    acl_id: str,
    data: LoginAssetAclUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LoginAssetAclResponse:
    _require_acl_permission(user, "acl:write")
    acl = await _get_scoped_login_asset_acl(db, actor_scope_from_user(user), acl_id)
    resource_type = data.resource_type if data.resource_type is not None else acl.resource_type
    ip_cidr = data.ip_cidr if data.ip_cidr is not None else acl.ip_cidr
    time_start = data.time_start if data.time_start is not None else acl.time_start
    time_end = data.time_end if data.time_end is not None else acl.time_end
    _validate_login_asset_fields(
        resource_type=resource_type,
        ip_cidr=ip_cidr,
        time_start=time_start,
        time_end=time_end,
    )
    if data.name is not None:
        acl.name = data.name
    if data.priority is not None:
        acl.priority = data.priority
    if data.action is not None:
        acl.action = data.action
    if data.resource_type is not None:
        acl.resource_type = data.resource_type
    if data.resource_id is not None:
        acl.resource_id = data.resource_id
    if data.ip_cidr is not None:
        acl.ip_cidr = _empty_to_none(data.ip_cidr)
    if data.time_start is not None:
        acl.time_start = _empty_to_none(data.time_start)
    if data.time_end is not None:
        acl.time_end = _empty_to_none(data.time_end)
    await db.commit()
    await db.refresh(acl)
    return _login_asset_acl_response(acl)


@router.delete("/login-asset-acls/{acl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_login_asset_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_acl_permission(user, "acl:write")
    acl = await _get_scoped_login_asset_acl(db, actor_scope_from_user(user), acl_id)
    await db.delete(acl)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/connect-method-acls/", response_model=ConnectMethodAclListResponse)
async def list_connect_method_acls(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectMethodAclListResponse:
    _require_acl_permission(user, "acl:read")
    result = await db.execute(
        scoped_select(ConnectMethodAclModel, actor_scope_from_user(user)).order_by(
            ConnectMethodAclModel.priority.asc(), ConnectMethodAclModel.id.asc()
        )
    )
    items = [_connect_method_acl_response(acl) for acl in result.scalars().all()]
    return ConnectMethodAclListResponse(items=items, total=len(items))


@router.post(
    "/connect-method-acls/",
    response_model=ConnectMethodAclResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connect_method_acl(
    data: ConnectMethodAclCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectMethodAclResponse:
    _require_acl_permission(user, "acl:write")
    resource_type, resource_id = _validate_connect_method_fields(
        protocol=data.protocol,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
    )
    acl = ConnectMethodAclModel(
        id=f"cma_{uuid4().hex}",
        tenant_id=str(user.get("tenant_id") or "default"),
        name=_overlay_name(data.name, data.protocol),
        priority=data.priority,
        action=data.action,
        protocol=data.protocol,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.add(acl)
    await db.commit()
    await db.refresh(acl)
    return _connect_method_acl_response(acl)


@router.get("/connect-method-acls/{acl_id}", response_model=ConnectMethodAclResponse)
async def get_connect_method_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectMethodAclResponse:
    _require_acl_permission(user, "acl:read")
    return _connect_method_acl_response(
        await _get_scoped_connect_method_acl(db, actor_scope_from_user(user), acl_id)
    )


@router.patch("/connect-method-acls/{acl_id}", response_model=ConnectMethodAclResponse)
async def update_connect_method_acl(
    acl_id: str,
    data: ConnectMethodAclUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectMethodAclResponse:
    _require_acl_permission(user, "acl:write")
    acl = await _get_scoped_connect_method_acl(db, actor_scope_from_user(user), acl_id)
    updates = data.model_dump(exclude_unset=True)
    protocol = updates.get("protocol", acl.protocol)
    resource_type = updates.get("resource_type", acl.resource_type)
    resource_id = updates.get("resource_id", acl.resource_id)
    resource_type, resource_id = _validate_connect_method_fields(
        protocol=protocol,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if data.name is not None:
        acl.name = data.name
    if data.priority is not None:
        acl.priority = data.priority
    if data.action is not None:
        acl.action = data.action
    if data.protocol is not None:
        acl.protocol = data.protocol
    acl.resource_type = resource_type
    acl.resource_id = resource_id
    await db.commit()
    await db.refresh(acl)
    return _connect_method_acl_response(acl)


@router.delete("/connect-method-acls/{acl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connect_method_acl(
    acl_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require_acl_permission(user, "acl:write")
    acl = await _get_scoped_connect_method_acl(db, actor_scope_from_user(user), acl_id)
    await db.delete(acl)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_acl_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _get_scoped_acl(
    db: AsyncSession, actor_scope: ActorScope, acl_id: str
) -> CommandFilterAclModel:
    result = await db.execute(
        scoped_select(CommandFilterAclModel, actor_scope).where(CommandFilterAclModel.id == acl_id)
    )
    acl = result.scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=404, detail="COMMAND_FILTER_ACL_NOT_FOUND")
    return acl


async def _get_scoped_masking_rule(
    db: AsyncSession, actor_scope: ActorScope, rule_id: str
) -> DataMaskingRuleModel:
    result = await db.execute(
        scoped_select(DataMaskingRuleModel, actor_scope).where(DataMaskingRuleModel.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="DATA_MASKING_RULE_NOT_FOUND")
    return rule


async def _load_groups_by_id(
    db: AsyncSession, actor_scope: ActorScope
) -> dict[str, CommandGroupModel]:
    result = await db.execute(scoped_select(CommandGroupModel, actor_scope))
    return {group.id: group for group in result.scalars().all()}


def _new_command_groups(
    tenant_id: str, payloads: list[CommandGroupPayload]
) -> list[CommandGroupModel]:
    return [
        CommandGroupModel(
            id=f"cg_{uuid4().hex}",
            tenant_id=tenant_id,
            name=payload.name,
            match_type=payload.match_type,
            patterns_json=_dump_ids(payload.patterns),
            is_active=True,
        )
        for payload in payloads
    ]


def _acl_response(
    acl: CommandFilterAclModel, groups_by_id: dict[str, CommandGroupModel]
) -> CommandFilterAclResponse:
    group_ids = _load_ids(acl.command_group_ids_json)
    groups = [
        CommandGroupResponse(
            id=group.id,
            tenant_id=group.tenant_id,
            name=group.name,
            match_type=group.match_type,
            patterns=_load_ids(group.patterns_json),
            is_active=group.is_active,
        )
        for group_id in group_ids
        if (group := groups_by_id.get(group_id)) is not None
    ]
    return CommandFilterAclResponse(
        id=acl.id,
        tenant_id=acl.tenant_id,
        name=acl.name,
        priority=acl.priority,
        action=acl.action,
        reviewer_subject_ids=_load_ids(acl.reviewer_subject_ids_json),
        subject_ids=_load_ids(acl.subject_ids_json),
        asset_ids=_load_ids(acl.asset_ids_json),
        account_ids=_load_ids(acl.account_ids_json),
        command_group_ids=group_ids,
        command_groups=groups,
        is_active=acl.is_active,
        created_at=_as_utc(acl.created_at),
        updated_at=_as_utc(acl.updated_at),
    )


def _masking_response(rule: DataMaskingRuleModel) -> DataMaskingRuleResponse:
    return DataMaskingRuleResponse(
        id=rule.id,
        tenant_id=rule.tenant_id,
        name=rule.name,
        priority=rule.priority,
        match_type=rule.match_type,
        patterns=_load_ids(rule.patterns_json),
        mask_method=rule.mask_method,
        keep_prefix=rule.keep_prefix,
        keep_suffix=rule.keep_suffix,
        placeholder=rule.placeholder,
        subject_ids=_load_ids(rule.subject_ids_json),
        asset_ids=_load_ids(rule.asset_ids_json),
        account_ids=_load_ids(rule.account_ids_json),
        is_active=rule.is_active,
        created_at=_as_utc(rule.created_at),
        updated_at=_as_utc(rule.updated_at),
    )


def _dump_ids(values: list[str]) -> str:
    return json.dumps(values)


def _load_ids(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)

async def _get_scoped_login_acl(
    db: AsyncSession, actor_scope: ActorScope, acl_id: str
) -> LoginAclModel:
    result = await db.execute(
        scoped_select(LoginAclModel, actor_scope).where(LoginAclModel.id == acl_id)
    )
    acl = result.scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=404, detail="LOGIN_ACL_NOT_FOUND")
    return acl


async def _get_scoped_login_asset_acl(
    db: AsyncSession, actor_scope: ActorScope, acl_id: str
) -> LoginAssetAclModel:
    result = await db.execute(
        scoped_select(LoginAssetAclModel, actor_scope).where(LoginAssetAclModel.id == acl_id)
    )
    acl = result.scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=404, detail="LOGIN_ASSET_ACL_NOT_FOUND")
    return acl


async def _get_scoped_connect_method_acl(
    db: AsyncSession, actor_scope: ActorScope, acl_id: str
) -> ConnectMethodAclModel:
    result = await db.execute(
        scoped_select(ConnectMethodAclModel, actor_scope).where(
            ConnectMethodAclModel.id == acl_id
        )
    )
    acl = result.scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=404, detail="CONNECT_METHOD_ACL_NOT_FOUND")
    return acl


async def _require_existing_login_subject(
    db: AsyncSession, actor_scope: ActorScope, subject_id: str
) -> User:
    user = await _find_user_by_subject(db, scoped_select(User, actor_scope), subject_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


async def _find_user_by_subject(db: AsyncSession, stmt: Any, subject_id: str) -> User | None:
    stmt = _apply_subject_id_filter(stmt, subject_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _apply_subject_id_filter(stmt: Any, subject_id: str) -> Any:
    if subject_id.isdigit():
        return stmt.where(User.id == int(subject_id))
    return stmt.where(cast(User.id, String) == subject_id)


async def _login_acl_response(db: AsyncSession, acl: LoginAclModel) -> LoginAclResponse:
    lookup = select(User).where(User.tenant_id == acl.tenant_id)
    user = await _find_user_by_subject(db, lookup, acl.subject_id)
    return LoginAclResponse(
        id=acl.id,
        tenant_id=acl.tenant_id,
        name=acl.name,
        priority=acl.priority,
        action=acl.action,
        subject_id=acl.subject_id,
        subject_username=user.username if user else "",
        created_at=_as_utc(acl.created_at),
        updated_at=_as_utc(acl.updated_at),
    )


def _login_asset_acl_response(acl: LoginAssetAclModel) -> LoginAssetAclResponse:
    return LoginAssetAclResponse(
        id=acl.id,
        tenant_id=acl.tenant_id,
        name=acl.name,
        priority=acl.priority,
        action=acl.action,
        resource_type=acl.resource_type,
        resource_id=acl.resource_id,
        ip_cidr=acl.ip_cidr,
        time_start=acl.time_start,
        time_end=acl.time_end,
        created_at=_as_utc(acl.created_at),
        updated_at=_as_utc(acl.updated_at),
    )


def _connect_method_acl_response(acl: ConnectMethodAclModel) -> ConnectMethodAclResponse:
    return ConnectMethodAclResponse(
        id=acl.id,
        tenant_id=acl.tenant_id,
        name=acl.name,
        priority=acl.priority,
        action=acl.action,
        protocol=acl.protocol,
        resource_type=acl.resource_type,
        resource_id=acl.resource_id,
        created_at=_as_utc(acl.created_at),
        updated_at=_as_utc(acl.updated_at),
    )


def _overlay_name(name: str, fallback: str) -> str:
    cleaned = name.strip()
    return cleaned or fallback[:128]


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _validate_login_asset_fields(
    *,
    resource_type: str,
    ip_cidr: str | None,
    time_start: str | None,
    time_end: str | None,
) -> None:
    if resource_type not in OVERLAY_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="resource_type 必须是 node 或 asset")
    cidr = (ip_cidr or "").strip()
    if cidr:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="ip_cidr 无效") from exc
    start_raw = (time_start or "").strip()
    end_raw = (time_end or "").strip()
    if start_raw or end_raw:
        if not start_raw or not end_raw:
            raise HTTPException(status_code=400, detail="时间窗口需同时提供开始和结束")
        start = _parse_hhmm(start_raw)
        end = _parse_hhmm(end_raw)
        if start is None or end is None:
            raise HTTPException(status_code=400, detail="时间窗口格式必须为 HH:MM")
        if start > end:
            raise HTTPException(status_code=400, detail="时间窗口不能跨天")


def _validate_connect_method_fields(
    *,
    protocol: str,
    resource_type: str | None,
    resource_id: str | None,
) -> tuple[str | None, str | None]:
    if protocol not in OVERLAY_PROTOCOLS:
        raise HTTPException(status_code=400, detail="protocol 必须是 ssh、k8s 或 sftp")
    rtype = _empty_to_none(resource_type)
    rid = _empty_to_none(resource_id)
    if rtype is None and rid is None:
        return None, None
    if rtype not in OVERLAY_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="resource_type 必须是 node、asset 或空")
    if not rid:
        raise HTTPException(status_code=400, detail="指定节点或资产时 resource_id 必填")
    return rtype, rid


def _parse_hhmm(value: str) -> time | None:
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return None
    return time(parsed.hour, parsed.minute)


