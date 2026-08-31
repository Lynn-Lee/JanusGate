"""#t65 命令过滤 ACL 与数据脱敏规则的租户隔离管理 API。

本模块只覆盖 roadmap 已落地的两类模型：``CommandFilterAclModel`` 与
``DataMaskingRuleModel``（命令组作为 ACL 的内嵌写入面一并持久化）。登录 ACL 不在本切片。

所有查询强制走 :func:`~app.tenancy.scope.scoped_select`，跨租户一律 404 fail-closed，
不泄露资源是否存在。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.acl_schemas import (
    CommandFilterAclCreate,
    CommandFilterAclListResponse,
    CommandFilterAclResponse,
    CommandFilterAclUpdate,
    CommandGroupPayload,
    CommandGroupResponse,
    DataMaskingRuleCreate,
    DataMaskingRuleListResponse,
    DataMaskingRuleResponse,
    DataMaskingRuleUpdate,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.acl import (
    CommandFilterAclModel,
    CommandGroupModel,
    DataMaskingRuleModel,
)
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
