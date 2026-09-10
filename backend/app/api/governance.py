"""#t79 平台治理 API：标签、动态配置、偏好、泄露密码库、报表目录。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.service import audit_service
from app.api.governance_schemas import (
    LabelBindingUpdate,
    LabelCreate,
    LabelListResponse,
    LabelResponse,
    LabelUpdate,
    LeakPasswordCheckRequest,
    LeakPasswordCheckResponse,
    LeakPasswordCreate,
    LeakPasswordListResponse,
    LeakPasswordResponse,
    PreferenceListResponse,
    PreferenceUpdateRequest,
    ReportCreate,
    ReportListResponse,
    ReportResponse,
    ReportRunRequest,
    ReportRunResponse,
    SettingItem,
    SettingListResponse,
    SettingRevisionListResponse,
    SettingRevisionResponse,
    SettingUpdateRequest,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.asset import Asset
from app.models.governance import (
    LeakPassword,
    ReportDefinition,
    ResourceLabel,
    ResourceLabelBinding,
    TenantSettingRevision,
)
from app.services.leak_passwords import (
    BUILTIN_LEAK_SHA256,
    leak_password_sha256,
    normalize_sha256,
    password_is_leaked,
)
from app.services.tenant_settings import (
    load_preference_map,
    load_setting_map,
    parse_setting_value,
    preference_items,
    setting_items,
    upsert_preferences,
    upsert_settings,
)
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select

router = APIRouter(prefix="/governance", tags=["平台治理"])

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ASSET_TYPE = "asset"
BUILTIN_REPORTS: tuple[tuple[str, str, str], ...] = (
    ("audit-summary", "审计汇总", "当前租户审计事件聚合计数，不含明细。"),
    ("soc2-access", "SOC2 访问合规", "已签名合规导出，只含事件 ID 与 hash chain 边界。"),
)
BUILTIN_TEMPLATE_KEYS = {item[0] for item in BUILTIN_REPORTS}


def _require(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _require_any(user: dict[str, Any], *permissions: str) -> None:
    granted = set(user.get("permissions", []))
    if "admin" in granted or granted.intersection(permissions):
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permissions[0]}")


def _validate_color(color: str) -> str:
    if not _COLOR_RE.fullmatch(color):
        raise HTTPException(status_code=400, detail="LABEL_COLOR_INVALID")
    return color


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _label_response(label: ResourceLabel, asset_ids: list[int]) -> LabelResponse:
    return LabelResponse(
        id=label.id,
        name=label.name,
        color=label.color,
        asset_ids=sorted(asset_ids),
    )


async def _bindings_by_label(
    db: AsyncSession, tenant_id: str, label_ids: list[int]
) -> dict[int, list[int]]:
    if not label_ids:
        return {}
    result = await db.execute(
        select(ResourceLabelBinding).where(
            ResourceLabelBinding.tenant_id == tenant_id,
            ResourceLabelBinding.label_id.in_(label_ids),
            ResourceLabelBinding.resource_type == _ASSET_TYPE,
        )
    )
    grouped: dict[int, list[int]] = {label_id: [] for label_id in label_ids}
    for row in result.scalars().all():
        grouped.setdefault(row.label_id, []).append(int(row.resource_id))
    return grouped


@router.get("/labels/", response_model=LabelListResponse)
async def list_labels(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LabelListResponse:
    _require_any(user, "assets:read", "assets:write")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(scoped_select(ResourceLabel, actor_scope).order_by(ResourceLabel.id.asc()))
    labels = list(result.scalars().all())
    grouped = await _bindings_by_label(db, actor_scope.tenant_id, [row.id for row in labels])
    items = [_label_response(row, grouped.get(row.id, [])) for row in labels]
    return LabelListResponse(items=items, total=len(items))


@router.post("/labels/", response_model=LabelResponse, status_code=status.HTTP_201_CREATED)
async def create_label(
    data: LabelCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LabelResponse:
    _require(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    label = ResourceLabel(
        tenant_id=actor_scope.tenant_id,
        name=name,
        color=_validate_color(data.color),
    )
    db.add(label)
    await db.commit()
    await db.refresh(label)
    return _label_response(label, [])


@router.patch("/labels/{label_id}", response_model=LabelResponse)
async def update_label(
    label_id: int,
    data: LabelUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LabelResponse:
    _require(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    label = await _get_label(db, actor_scope, label_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        label.name = name
    if "color" in payload and payload["color"] is not None:
        label.color = _validate_color(str(payload["color"]))
    await db.commit()
    await db.refresh(label)
    grouped = await _bindings_by_label(db, actor_scope.tenant_id, [label.id])
    return _label_response(label, grouped.get(label.id, []))


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(
    label_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> Response:
    _require(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    label = await _get_label(db, actor_scope, label_id)
    bindings = await db.execute(
        select(ResourceLabelBinding).where(
            ResourceLabelBinding.tenant_id == actor_scope.tenant_id,
            ResourceLabelBinding.label_id == label.id,
        )
    )
    for row in bindings.scalars().all():
        await db.delete(row)
    await db.delete(label)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/labels/{label_id}/assets", response_model=LabelResponse)
async def replace_label_assets(
    label_id: int,
    data: LabelBindingUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LabelResponse:
    _require(user, "assets:write")
    actor_scope = actor_scope_from_user(user)
    label = await _get_label(db, actor_scope, label_id)
    unique_ids = sorted({int(item) for item in data.asset_ids})
    if unique_ids:
        assets = await db.execute(
            scoped_select(Asset, actor_scope).where(Asset.id.in_(unique_ids))
        )
        found = {row.id for row in assets.scalars().all()}
        if found != set(unique_ids):
            raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
    existing = await db.execute(
        select(ResourceLabelBinding).where(
            ResourceLabelBinding.tenant_id == actor_scope.tenant_id,
            ResourceLabelBinding.label_id == label.id,
            ResourceLabelBinding.resource_type == _ASSET_TYPE,
        )
    )
    for row in existing.scalars().all():
        await db.delete(row)
    for asset_id in unique_ids:
        db.add(
            ResourceLabelBinding(
                tenant_id=actor_scope.tenant_id,
                label_id=label.id,
                resource_type=_ASSET_TYPE,
                resource_id=str(asset_id),
            )
        )
    await db.commit()
    return _label_response(label, unique_ids)


@router.get("/settings", response_model=SettingListResponse)
async def get_settings(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SettingListResponse:
    _require(user, "admin")
    actor_scope = actor_scope_from_user(user)
    values = await load_setting_map(db, actor_scope.tenant_id)
    return SettingListResponse(items=[SettingItem(**item) for item in setting_items(values)])


@router.put("/settings", response_model=SettingListResponse)
async def put_settings(
    data: SettingUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> SettingListResponse:
    _require(user, "admin")
    actor_scope = actor_scope_from_user(user)
    updates = {item.key: item.value for item in data.items}
    try:
        values = await upsert_settings(
            db,
            tenant_id=actor_scope.tenant_id,
            actor_id=actor_scope.user_id,
            updates=updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SettingListResponse(items=[SettingItem(**item) for item in setting_items(values)])


@router.get("/settings/revisions", response_model=SettingRevisionListResponse)
async def list_setting_revisions(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> SettingRevisionListResponse:
    _require(user, "admin")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(TenantSettingRevision, actor_scope).order_by(TenantSettingRevision.id.desc())
    )
    rows = list(result.scalars().all())
    items = [
        SettingRevisionResponse(
            id=row.id,
            key=row.key,
            old_value=parse_setting_value(row.old_value_json),
            new_value=parse_setting_value(row.new_value_json),
            actor_id=row.actor_id,
            created_at=_iso(row.created_at),
        )
        for row in rows
    ]
    return SettingRevisionListResponse(items=items, total=len(items))


@router.get("/preferences", response_model=PreferenceListResponse)
async def get_preferences(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> PreferenceListResponse:
    actor_scope = actor_scope_from_user(user)
    values = await load_preference_map(
        db, tenant_id=actor_scope.tenant_id, user_id=actor_scope.user_id
    )
    return PreferenceListResponse(items=[SettingItem(**item) for item in preference_items(values)])


@router.put("/preferences", response_model=PreferenceListResponse)
async def put_preferences(
    data: PreferenceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> PreferenceListResponse:
    actor_scope = actor_scope_from_user(user)
    updates = {item.key: item.value for item in data.items}
    try:
        values = await upsert_preferences(
            db,
            tenant_id=actor_scope.tenant_id,
            user_id=actor_scope.user_id,
            updates=updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PreferenceListResponse(items=[SettingItem(**item) for item in preference_items(values)])


@router.get("/leak-passwords", response_model=LeakPasswordListResponse)
async def list_leak_passwords(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LeakPasswordListResponse:
    _require(user, "admin")
    actor_scope = actor_scope_from_user(user)
    result = await db.execute(
        scoped_select(LeakPassword, actor_scope).order_by(LeakPassword.id.asc())
    )
    rows = list(result.scalars().all())
    items = [
        LeakPasswordResponse(id=row.id, sha256=row.sha256, created_at=_iso(row.created_at))
        for row in rows
    ]
    return LeakPasswordListResponse(
        items=items, total=len(items), builtin_count=len(BUILTIN_LEAK_SHA256)
    )


@router.post(
    "/leak-passwords",
    response_model=LeakPasswordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_leak_password(
    data: LeakPasswordCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> LeakPasswordResponse:
    _require(user, "admin")
    actor_scope = actor_scope_from_user(user)
    digest = _digest_from_create(data)
    existing = await db.execute(
        select(LeakPassword).where(
            LeakPassword.tenant_id == actor_scope.tenant_id,
            LeakPassword.sha256 == digest,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = LeakPassword(
            tenant_id=actor_scope.tenant_id,
            sha256=digest,
            created_by=actor_scope.user_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return LeakPasswordResponse(id=row.id, sha256=row.sha256, created_at=_iso(row.created_at))


@router.post("/leak-passwords/check", response_model=LeakPasswordCheckResponse)
async def check_leak_password(
    data: LeakPasswordCheckRequest,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> LeakPasswordCheckResponse:
    _require(user, "admin")
    actor_scope = actor_scope_from_user(user)
    leaked = await password_is_leaked(
        db, tenant_id=actor_scope.tenant_id, password=data.password
    )
    return LeakPasswordCheckResponse(leaked=leaked)


@router.get("/reports", response_model=ReportListResponse)
async def list_reports(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> ReportListResponse:
    _require_any(user, "audit:read", "audit:write")
    actor_scope = actor_scope_from_user(user)
    builtins = [
        ReportResponse(
            id=None,
            name=name,
            template_key=template_key,
            description=description,
            builtin=True,
        )
        for template_key, name, description in BUILTIN_REPORTS
    ]
    result = await db.execute(
        scoped_select(ReportDefinition, actor_scope).order_by(ReportDefinition.id.asc())
    )
    saved = [
        ReportResponse(
            id=row.id,
            name=row.name,
            template_key=row.template_key,
            description=row.description,
            builtin=False,
        )
        for row in result.scalars().all()
    ]
    items = [*builtins, *saved]
    return ReportListResponse(items=items, total=len(items))


@router.post("/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    data: ReportCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ReportResponse:
    _require(user, "audit:write")
    if data.template_key not in BUILTIN_TEMPLATE_KEYS:
        raise HTTPException(status_code=400, detail="REPORT_TEMPLATE_UNKNOWN")
    actor_scope = actor_scope_from_user(user)
    row = ReportDefinition(
        tenant_id=actor_scope.tenant_id,
        name=data.name.strip(),
        template_key=data.template_key,
        description=data.description.strip(),
        created_by=actor_scope.user_id,
    )
    if not row.name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ReportResponse(
        id=row.id,
        name=row.name,
        template_key=row.template_key,
        description=row.description,
        builtin=False,
    )


@router.post("/reports/run", response_model=ReportRunResponse)
async def run_report(
    data: ReportRunRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ReportRunResponse:
    _require_any(user, "audit:read", "audit:write")
    actor_scope = actor_scope_from_user(user)
    template_key = data.template_key
    if data.report_id is not None:
        result = await db.execute(
            scoped_select(ReportDefinition, actor_scope).where(ReportDefinition.id == data.report_id)
        )
        saved = result.scalar_one_or_none()
        if saved is None:
            raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
        template_key = saved.template_key
    if template_key is None:
        raise HTTPException(status_code=400, detail="REPORT_TEMPLATE_REQUIRED")
    if template_key not in BUILTIN_TEMPLATE_KEYS:
        raise HTTPException(status_code=400, detail="REPORT_TEMPLATE_UNKNOWN")
    payload = await _execute_report(template_key=template_key, tenant_id=actor_scope.tenant_id)
    return ReportRunResponse(template_key=template_key, result=payload)


async def _execute_report(*, template_key: str, tenant_id: str) -> dict[str, Any]:
    if template_key == "audit-summary":
        summary = await audit_service.report_summary(tenant_id=tenant_id)
        return summary.model_dump(mode="json")
    report = await audit_service.compliance_report(tenant_id=tenant_id, template=template_key)
    dumped = report.model_dump(mode="json")
    for forbidden in ("metadata", "message", "resource_id", "session_id"):
        dumped.pop(forbidden, None)
    return dumped


async def _get_label(db: AsyncSession, actor_scope: ActorScope, label_id: int) -> ResourceLabel:
    result = await db.execute(
        scoped_select(ResourceLabel, actor_scope).where(ResourceLabel.id == label_id)
    )
    label = result.scalar_one_or_none()
    if label is None:
        raise HTTPException(status_code=404, detail="LABEL_NOT_FOUND")
    return label


def _digest_from_create(data: LeakPasswordCreate) -> str:
    if not data.password and not data.sha256:
        raise HTTPException(status_code=400, detail="LEAK_PASSWORD_INPUT_REQUIRED")
    if data.password and data.sha256:
        digest = leak_password_sha256(data.password)
        try:
            provided = normalize_sha256(data.sha256)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if digest != provided:
            raise HTTPException(status_code=400, detail="LEAK_PASSWORD_HASH_MISMATCH")
        return digest
    if data.password:
        return leak_password_sha256(data.password)
    try:
        return normalize_sha256(str(data.sha256))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

