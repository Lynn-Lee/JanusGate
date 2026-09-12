"""#t74 TicketFlow multi-level approval tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.workflows.service import (
    InMemoryWorkflowStore,
    WorkflowRequestStatus,
    WorkflowService,
)
from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.workflows.ticket_flows import (
    ApprovalLevelSnapshot,
    TicketFlowRepository,
    TicketFlowSnapshot,
)


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


def _flow(levels: list[list[str]], *, enabled: bool = True) -> TicketFlowSnapshot:
    return TicketFlowSnapshot(
        id="tf-1",
        tenant_id="tenant-1",
        name="固定资产授权",
        flow_type="asset_grant",
        enabled=enabled,
        levels=[
            ApprovalLevelSnapshot(level=index, approver_user_ids=users)
            for index, users in enumerate(levels, start=1)
        ],
    )


@pytest.mark.asyncio
async def test_no_enabled_flow_keeps_single_step_approve_and_grant() -> None:
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="排障",
        requested_ttl_seconds=1800,
        metadata={},
    )
    submitted = await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    assert submitted.total_levels == 0
    assert submitted.steps == []
    approved = await service.approve_request(
        "wr-1",
        actor={
            "id": "approver-1",
            "username": "bob",
            "tenant_id": "tenant-1",
            "permissions": ["workflow:approve"],
        },
        decision_reason="ok",
        grant_ttl_seconds=1800,
    )
    assert approved.status is WorkflowRequestStatus.APPROVED
    assert approved.grant_id == "grant-1"


@pytest.mark.asyncio
async def test_multi_level_all_pass_then_issues_grant() -> None:
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    service.set_enabled_ticket_flow("tenant-1", _flow([["a1"], ["a2"]]))
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="排障",
        requested_ttl_seconds=1800,
        metadata={},
    )
    submitted = await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    assert submitted.total_levels == 2
    assert submitted.current_level == 1
    assert [step.status for step in submitted.steps] == ["pending", "not_started"]

    mid = await service.approve_request(
        "wr-1",
        actor={"id": "a1", "username": "l1", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="L1 ok",
        grant_ttl_seconds=1800,
    )
    assert mid.status is WorkflowRequestStatus.PENDING
    assert mid.current_level == 2
    assert mid.grant_id == ""
    assert [step.status for step in mid.steps] == ["approved", "pending"]

    final = await service.approve_request(
        "wr-1",
        actor={"id": "a2", "username": "l2", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="L2 ok",
        grant_ttl_seconds=1800,
    )
    assert final.status is WorkflowRequestStatus.APPROVED
    assert final.grant_id == "grant-1"
    grant = await service.get_grant("grant-1", tenant_id="tenant-1")
    assert grant is not None


@pytest.mark.asyncio
async def test_any_level_reject_closes_ticket_without_grant() -> None:
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    service.set_enabled_ticket_flow("tenant-1", _flow([["a1"], ["a2"]]))
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="排障",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    rejected = await service.reject_request(
        "wr-1",
        actor={"id": "a1", "username": "l1", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="no",
    )
    assert rejected.status is WorkflowRequestStatus.REJECTED
    assert rejected.grant_id == ""
    assert await service.get_grant("grant-1", tenant_id="tenant-1") is None


@pytest.mark.asyncio
async def test_multi_level_keeps_anti_self_approval() -> None:
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    service.set_enabled_ticket_flow("tenant-1", _flow([["user-1"], ["a2"]]))
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="排障",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    with pytest.raises(PermissionError, match="SELF_APPROVAL_NOT_ALLOWED"):
        await service.approve_request(
            "wr-1",
            actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []},
            decision_reason="self",
            grant_ttl_seconds=1800,
        )


@pytest.mark.asyncio
async def test_ticket_flow_api_crud_and_in_progress_delete_block() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-1",
        "username": "admin",
        "tenant_id": "tenant-1",
        "permissions": ["workflow:admin", "workflow:approve"],
    }
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/workflows/ticket-flows",
                json={
                    "name": "两级审批",
                    "enabled": True,
                    "levels": [
                        {"approver_user_ids": ["a1"]},
                        {"approver_user_ids": ["a2", "a3"]},
                    ],
                },
            )
            assert created.status_code == 201, created.text
            flow_id = created.json()["id"]
            assert created.json()["level_count"] == 2
            assert created.json()["enabled"] is True

            listed = client.get("/api/v1/workflows/ticket-flows")
            assert listed.status_code == 200
            assert listed.json()["total"] == 1

            # create in-progress ticket bound to flow
            async with session_factory() as session:
                from app.api.workflows.service import SQLAlchemyWorkflowStore

                repo = TicketFlowRepository(session)
                service = WorkflowService(
                    store=SQLAlchemyWorkflowStore(session),
                    audit_sink=FakeAuditSink(),
                    ticket_flow_loader=lambda tenant_id: repo.get_enabled_asset_grant_flow(
                        tenant_id=tenant_id
                    ),
                    now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
                    request_id_factory=lambda: "wr-flow-1",
                    grant_id_factory=lambda: "grant-flow-1",
                )
                await service.create_request(
                    actor={
                        "id": "user-1",
                        "username": "alice",
                        "tenant_id": "tenant-1",
                        "permissions": [],
                    },
                    asset_id="asset-1",
                    account_id="root",
                    protocol="ssh",
                    action="session.connect",
                    reason="排障",
                    requested_ttl_seconds=1800,
                    metadata={},
                )
                await service.submit_request(
                    "wr-flow-1", actor_id="user-1", tenant_id="tenant-1"
                )

            blocked = client.delete(f"/api/v1/workflows/ticket-flows/{flow_id}")
            assert blocked.status_code == 400
            assert blocked.json()["detail"] == "有进行中的工单"

            # disable by enabling another flow — second create enabled should disable first
            second = client.post(
                "/api/v1/workflows/ticket-flows",
                json={
                    "name": "备用",
                    "enabled": True,
                    "levels": [{"approver_user_ids": ["b1"]}],
                },
            )
            assert second.status_code == 201
            listed2 = client.get("/api/v1/workflows/ticket-flows")
            enabled_flags = {item["id"]: item["enabled"] for item in listed2.json()["items"]}
            assert enabled_flags[flow_id] is False
            assert enabled_flags[second.json()["id"]] is True
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_ticket_flow_api_accepts_command_review_type() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-1",
        "username": "admin",
        "tenant_id": "tenant-1",
        "permissions": ["workflow:admin", "workflow:approve"],
    }
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/workflows/ticket-flows",
                json={
                    "name": "命令复核",
                    "flow_type": "command_review",
                    "enabled": True,
                    "levels": [{"approver_user_ids": ["a1"]}],
                },
            )
            assert created.status_code == 201, created.text
            assert created.json()["flow_type"] == "command_review"
            # asset_grant can still be enabled independently
            asset = client.post(
                "/api/v1/workflows/ticket-flows",
                json={
                    "name": "资产授权",
                    "flow_type": "asset_grant",
                    "enabled": True,
                    "levels": [{"approver_user_ids": ["b1"]}],
                },
            )
            assert asset.status_code == 201, asset.text
            listed = client.get("/api/v1/workflows/ticket-flows")
            by_id = {item["id"]: item for item in listed.json()["items"]}
            assert by_id[created.json()["id"]]["enabled"] is True
            assert by_id[asset.json()["id"]]["enabled"] is True
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

