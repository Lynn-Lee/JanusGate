"""#t64：节点 / AssetPermission 经 scoped_select 装载，供 evaluate 使用。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.asset import Asset
from app.models.asset_tree import (
    ASSET_RESOURCE,
    CONNECT_ACTION,
    NODE_RESOURCE,
    AssetPermissionModel,
    NodeModel,
)
from app.policy.repository import build_tenant_policy_service
from app.policy.schemas import PolicyDecision, PolicyDecisionRequest, ResourceRef, SubjectRef
from app.tenancy.scope import ActorScope


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _scope(tenant_id: str = "tenant-a") -> ActorScope:
    return ActorScope(user_id="user-1", tenant_id=tenant_id)


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            NodeModel(
                id="root-a",
                tenant_id="tenant-a",
                parent_id=None,
                name="根",
                ancestor_ids_json="[]",
            ),
            NodeModel(
                id="folder-a",
                tenant_id="tenant-a",
                parent_id="root-a",
                name="folder",
                ancestor_ids_json=json.dumps(["root-a"]),
            ),
            NodeModel(
                id="root-b",
                tenant_id="tenant-b",
                parent_id=None,
                name="根",
                ancestor_ids_json="[]",
            ),
            Asset(
                id=10,
                name="leaf-host",
                address="10.0.0.10",
                tenant_id="tenant-a",
                platform_id=1,
                node_id="folder-a",
            ),
            Asset(
                id=11,
                name="other-host",
                address="10.0.0.11",
                tenant_id="tenant-a",
                platform_id=1,
                node_id=None,
            ),
            Asset(
                id=20,
                name="other-tenant",
                address="10.0.0.20",
                tenant_id="tenant-b",
                platform_id=1,
                node_id=None,
            ),
            AssetPermissionModel(
                id="ap-folder",
                tenant_id="tenant-a",
                subject_id="user-1",
                resource_type=NODE_RESOURCE,
                resource_id="folder-a",
                account_id="",
                protocol="",
                action=CONNECT_ACTION,
                expires_at=None,
            ),
            AssetPermissionModel(
                id="ap-other-tenant",
                tenant_id="tenant-b",
                subject_id="user-1",
                resource_type=ASSET_RESOURCE,
                resource_id="20",
                account_id="",
                protocol="",
                action=CONNECT_ACTION,
                expires_at=None,
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_build_service_scopes_permissions_and_evaluates_connect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed(session)
        service = await build_tenant_policy_service(session, _scope("tenant-a"))

    allowed = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="10", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            connector_trusted=True,
        )
    )
    denied_ungrouped = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="11", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            connector_trusted=True,
        )
    )
    other_tenant = service.evaluate(
        PolicyDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="20", type="asset", tenant_id="tenant-a"),
            action="session.connect",
            connector_trusted=True,
        )
    )
    assert allowed.decision is PolicyDecision.ALLOW
    assert allowed.reason_code == "ASSET_PERMISSION_ALLOWED"
    assert any("permission:ap-folder" in line for line in allowed.explain_trace)
    assert denied_ungrouped.decision is PolicyDecision.DENY
    assert other_tenant.decision is PolicyDecision.DENY
