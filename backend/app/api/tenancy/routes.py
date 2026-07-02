"""Phase 4 tenancy management API routes."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenancy.schemas import (
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationResponse,
)
from app.core.database import get_db
from app.core.deps import current_user
from app.models.tenancy import Organization
from app.tenancy.scope import actor_scope_from_user, scoped_select

router = APIRouter(prefix="/tenancy", tags=["多租户"])


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> OrganizationListResponse:
    _require_tenancy_permission(user, "tenancy:read")
    result = await db.execute(scoped_select(Organization, actor_scope_from_user(user)))
    organizations = result.scalars().all()
    items = [
        OrganizationResponse(
            id=organization.id,
            tenant_id=organization.tenant_id,
            name=organization.name,
            status=organization.status,
        )
        for organization in organizations
    ]
    return OrganizationListResponse(items=items, total=len(items))


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    data: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> OrganizationResponse:
    if "admin" not in user.get("permissions", []):
        raise HTTPException(status_code=403, detail="缺少权限: admin")

    tenant_id = str(user.get("tenant_id") or "default")
    existing = await db.get(Organization, data.id)
    if existing is not None and existing.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="TENANT_SCOPE_VIOLATION")
    if existing is not None:
        raise HTTPException(status_code=400, detail="ORGANIZATION_ALREADY_EXISTS")

    organization = Organization(
        id=data.id,
        tenant_id=tenant_id,
        name=data.name,
        status=data.status,
    )
    db.add(organization)
    await db.commit()
    await db.refresh(organization)
    return OrganizationResponse(
        id=organization.id,
        tenant_id=organization.tenant_id,
        name=organization.name,
        status=organization.status,
    )


def _require_tenancy_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
