from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.workflow import ApproverMode, JitGrantStatus, WorkflowRequestStatus
from app.workflows.repository import SQLAlchemyWorkflowRepository


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _create_pending_request(repo: SQLAlchemyWorkflowRepository):
    request = await repo.create_request(
        tenant_id="tenant-a",
        requester_id="user-1",
        requester_username="alice",
        resource_type="asset",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="incident response",
        requested_ttl_seconds=1800,
        metadata={"ticket_id": "INC-1"},
    )
    await repo.submit_request(request.id, tenant_id="tenant-a")
    return request


async def test_sqlalchemy_repository_persists_request_and_grant_transaction(session_factory):
    async with session_factory.begin() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        request = await _create_pending_request(repo)
        grant = await repo.approve_request(
            request.id,
            tenant_id="tenant-a",
            approver_id="manager-1",
            approver_username="manager",
            decision_reason="approved",
            grant_ttl_seconds=900,
            max_session_ttl_seconds=600,
            constraints={"usage": "single-use"},
        )
        grant_id = grant.id

    async with session_factory() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        requests = await repo.list_requests(tenant_id="tenant-a", requester_id="user-1")
        assert len(requests) == 1
        assert requests[0].status == WorkflowRequestStatus.approved
        active_grant = await repo.find_active_grant(
            tenant_id="tenant-a",
            subject_id="user-1",
            asset_id="asset-1",
            account_id="root",
            protocol="ssh",
            action="session.connect",
            now=datetime.now(UTC),
        )
        assert active_grant is not None
        assert active_grant.id == grant_id


async def test_sqlalchemy_repository_rolls_back_failed_transaction(session_factory):
    with pytest.raises(RuntimeError, match="boom"):
        async with session_factory.begin() as session:
            repo = SQLAlchemyWorkflowRepository(session)
            await repo.create_request(
                tenant_id="tenant-a",
                requester_id="user-rollback",
                requester_username="alice",
                resource_type="asset",
                asset_id="asset-1",
                account_id="root",
                protocol="ssh",
                action="session.connect",
                reason="incident response",
                requested_ttl_seconds=1800,
                metadata={},
            )
            raise RuntimeError("boom")

    async with session_factory() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        assert await repo.list_requests(tenant_id="tenant-a", requester_id="user-rollback") == []


async def test_sqlalchemy_repository_manages_approval_policies(session_factory):
    async with session_factory.begin() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        await repo.create_approval_policy(
            tenant_id="tenant-a",
            resource_selector={"asset_id": "asset-1"},
            action_selector="session.connect",
            approver_subject_ids=["manager-1"],
            approver_mode=ApproverMode.named_user,
            max_grant_ttl_seconds=900,
            risk_level="high",
        )
        await repo.create_approval_policy(
            tenant_id="tenant-b",
            resource_selector={},
            action_selector="session.connect",
            approver_subject_ids=["manager-2"],
        )

    async with session_factory() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        policies = await repo.list_approval_policies(tenant_id="tenant-a")
        assert len(policies) == 1
        assert policies[0].action_selector == "session.connect"
        assert policies[0].risk_level == "high"


async def test_sqlalchemy_repository_updates_grant_use_revoke_and_expire(session_factory):
    async with session_factory.begin() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        request = await _create_pending_request(repo)
        grant = await repo.approve_request(
            request.id,
            tenant_id="tenant-a",
            approver_id="manager-1",
            approver_username="manager",
            decision_reason="approved",
            grant_ttl_seconds=900,
            max_session_ttl_seconds=600,
            constraints={},
        )
        grant_id = grant.id

    async with session_factory.begin() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        used = await repo.mark_grant_used(grant_id, tenant_id="tenant-a")
        assert used.status == JitGrantStatus.used
        revoked = await repo.revoke_grant(grant_id, tenant_id="tenant-a")
        assert revoked.status == JitGrantStatus.revoked
        assert revoked.revoked_at is not None

    async with session_factory.begin() as session:
        repo = SQLAlchemyWorkflowRepository(session)
        request = await _create_pending_request(repo)
        grant = await repo.approve_request(
            request.id,
            tenant_id="tenant-a",
            approver_id="manager-1",
            approver_username="manager",
            decision_reason="approved",
            grant_ttl_seconds=900,
            max_session_ttl_seconds=600,
            constraints={},
        )
        expired = await repo.expire_grant(grant.id, tenant_id="tenant-a")
        assert expired.status == JitGrantStatus.expired
        assert await repo.find_active_grant(
            tenant_id="tenant-a",
            subject_id="user-1",
            asset_id="asset-1",
            account_id="root",
            protocol="ssh",
            action="session.connect",
            now=datetime.now(UTC),
        ) is None
