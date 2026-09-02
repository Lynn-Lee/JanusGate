"""Phase 4 account custody API routes."""
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account_schemas import (
    AccountCreate,
    AccountListResponse,
    AccountResponse,
    CredentialRotationCreate,
    CredentialRotationListResponse,
    CredentialRotationResponse,
)
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.k8s.service import validate_k8s_account_fields
from app.k8s.validation import load_namespaces
from app.models.account import Account, CredentialRotation
from app.models.asset import Asset
from app.models.tenancy import Organization, Project, Team
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/accounts", tags=["账号托管"])


@router.get("/", response_model=AccountListResponse)
async def list_accounts(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountListResponse:
    _require_account_permission(user, "accounts:read")
    result = await db.execute(scoped_select(Account, actor_scope_from_user(user)))
    accounts = result.scalars().all()
    items = [_account_response(account) for account in accounts]
    return AccountListResponse(items=items, total=len(items))


@router.post("/", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    data: AccountCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> AccountResponse:
    _require_account_permission(user, "accounts:write")
    tenant_id = str(user.get("tenant_id") or "default")

    asset = await db.get(Asset, data.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")

    await _assert_tenant_scope(
        db=db,
        tenant_id=tenant_id,
        organization_id=data.organization_id,
        team_id=data.team_id,
        project_id=data.project_id,
    )

    try:
        validate_k8s_account_fields(
            protocol=data.protocol,
            secret_id=data.secret_id,
            k8s_namespaces=data.k8s_namespaces,
            k8s_service_account=data.k8s_service_account,
            k8s_token_ttl_seconds=data.k8s_token_ttl_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    account = Account(
        tenant_id=tenant_id,
        asset_id=data.asset_id,
        username=data.username,
        protocol=data.protocol,
        secret_id=data.secret_id,
        organization_id=data.organization_id,
        team_id=data.team_id,
        project_id=data.project_id,
        status=data.status,
        rotation_policy=data.rotation_policy,
        k8s_namespaces_json=json.dumps(data.k8s_namespaces),
        k8s_service_account=data.k8s_service_account,
        k8s_default_pod=data.k8s_default_pod,
        k8s_default_container=data.k8s_default_container,
        k8s_use_short_lived_token=data.k8s_use_short_lived_token,
        k8s_token_ttl_seconds=data.k8s_token_ttl_seconds,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return _account_response(account)


@router.get("/{account_id}/rotations", response_model=CredentialRotationListResponse)
async def list_credential_rotations(
    account_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> CredentialRotationListResponse:
    _require_account_permission(user, "accounts:read")
    account = await _get_scoped_account(db=db, user=user, account_id=account_id)
    result = await db.execute(
        scoped_select(CredentialRotation, actor_scope_from_user(user))
        .where(CredentialRotation.account_id == account.id)
        .order_by(CredentialRotation.id)
    )
    rotations = result.scalars().all()
    items = [_rotation_response(rotation) for rotation in rotations]
    return CredentialRotationListResponse(items=items, total=len(items))


@router.post(
    "/{account_id}/rotations",
    response_model=CredentialRotationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def schedule_credential_rotation(
    account_id: int,
    data: CredentialRotationCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> CredentialRotationResponse:
    _require_account_permission(user, "accounts:rotate")
    account = await _get_scoped_account(db=db, user=user, account_id=account_id)
    rotation = CredentialRotation(
        tenant_id=account.tenant_id,
        account_id=account.id,
        status="scheduled",
        reason=data.reason,
        requested_by=str(user.get("id") or ""),
        scheduled_at=data.scheduled_at,
    )
    db.add(rotation)
    await db.commit()
    await db.refresh(rotation)
    return _rotation_response(rotation)


async def _assert_tenant_scope(
    *,
    db: AsyncSession,
    tenant_id: str,
    organization_id: str | None,
    team_id: str | None,
    project_id: str | None,
) -> None:
    if organization_id is not None:
        organization = await db.get(Organization, organization_id)
        if organization is not None and organization.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="TENANT_SCOPE_VIOLATION")
        if organization is None:
            raise HTTPException(status_code=404, detail="ORGANIZATION_NOT_FOUND")

    if team_id is not None:
        team = await db.get(Team, team_id)
        if team is not None and (
            team.tenant_id != tenant_id
            or (organization_id is not None and team.organization_id != organization_id)
        ):
            raise HTTPException(status_code=403, detail="TENANT_SCOPE_VIOLATION")
        if team is None:
            raise HTTPException(status_code=404, detail="TEAM_NOT_FOUND")

    if project_id is not None:
        project = await db.get(Project, project_id)
        if project is not None and (
            project.tenant_id != tenant_id
            or (organization_id is not None and project.organization_id != organization_id)
            or (team_id is not None and project.team_id != team_id)
        ):
            raise HTTPException(status_code=403, detail="TENANT_SCOPE_VIOLATION")
        if project is None:
            raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")


def _require_account_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


async def _get_scoped_account(
    *, db: AsyncSession, user: dict[str, Any], account_id: int
) -> Account:
    result = await db.execute(
        scoped_select(Account, actor_scope_from_user(user)).where(Account.id == account_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="ACCOUNT_NOT_FOUND")
    return account


def _account_response(account: Account) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        tenant_id=account.tenant_id,
        asset_id=account.asset_id,
        username=account.username,
        protocol=account.protocol,
        secret_id=account.secret_id,
        organization_id=account.organization_id,
        team_id=account.team_id,
        project_id=account.project_id,
        status=account.status,
        rotation_policy=account.rotation_policy,
        k8s_namespaces=load_namespaces(account.k8s_namespaces_json),
        k8s_service_account=account.k8s_service_account,
        k8s_default_pod=account.k8s_default_pod,
        k8s_default_container=account.k8s_default_container,
        k8s_use_short_lived_token=account.k8s_use_short_lived_token,
        k8s_token_ttl_seconds=account.k8s_token_ttl_seconds,
    )


def _rotation_response(rotation: CredentialRotation) -> CredentialRotationResponse:
    return CredentialRotationResponse(
        id=rotation.id,
        tenant_id=rotation.tenant_id,
        account_id=rotation.account_id,
        status=rotation.status,
        reason=rotation.reason,
        requested_by=rotation.requested_by,
        scheduled_at=_as_utc(rotation.scheduled_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
