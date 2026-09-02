"""Tenant row helpers (get-or-create). Not a public settings API."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenancy import DEFAULT_TENANT_TIMEZONE, Tenant


async def ensure_tenant(session: AsyncSession, tenant_id: str) -> Tenant:
    """Load or create the tenant row with default timezone Asia/Singapore.

    Used when assembling policy / login-asset ACL evaluation so connect
    decisions follow the tenant's current zone. Not a settings UI.
    """

    tid = (tenant_id or "").strip() or "default"
    result = await session.execute(select(Tenant).where(Tenant.id == tid))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant
    tenant = Tenant(id=tid, timezone=DEFAULT_TENANT_TIMEZONE)
    session.add(tenant)
    await session.flush()
    return tenant
