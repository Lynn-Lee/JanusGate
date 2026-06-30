"""Workflow/JIT request API and state-machine tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.workflows.routes import get_workflow_service
from app.api.workflows.service import (
    InMemoryWorkflowStore,
    JitGrantStatus,
    WorkflowRequestStatus,
    WorkflowService,
)
from app.core.deps import current_user
from app.main import app


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


class FakeSessionRevoker:
    def __init__(self) -> None:
        self.revoked_grants: list[str] = []

    async def revoke_sessions_by_jit_grant(self, jit_grant_id: str, reason: str) -> list[str]:
        self.revoked_grants.append(f"{jit_grant_id}:{reason}")
        return ["session-1"]


def build_workflow_service() -> tuple[WorkflowService, FakeAuditSink, FakeSessionRevoker]:
    audit = FakeAuditSink()
    revoker = FakeSessionRevoker()
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=audit,
        session_revoker=revoker,
        now=lambda: datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    return service, audit, revoker


@pytest.mark.asyncio
async def test_workflow_submit_and_approve_creates_active_jit_grant() -> None:
    service, audit, _revoker = build_workflow_service()

    request = await service.create_request(
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
        reason="数据库故障排查",
        requested_ttl_seconds=1800,
        metadata={"ticket_id": "INC-1001"},
    )
    assert request.status is WorkflowRequestStatus.DRAFT
    submitted = await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    assert submitted.status is WorkflowRequestStatus.PENDING
    approved = await service.approve_request(
        "wr-1",
        actor={
            "id": "approver-1",
            "username": "bob",
            "tenant_id": "tenant-1",
            "permissions": ["workflow:approve"],
        },
        decision_reason="允许 30 分钟排障",
        grant_ttl_seconds=1800,
    )

    assert approved.status is WorkflowRequestStatus.APPROVED
    assert approved.grant_id == "grant-1"
    grant = await service.get_grant("grant-1", tenant_id="tenant-1")
    assert grant is not None
    assert grant.subject_id == "user-1"
    assert grant.asset_id == "asset-1"
    assert grant.account_id == "root"
    assert grant.protocol == "ssh"
    assert grant.action == "session.connect"
    assert [event["type"] for event in audit.events] == [
        "workflow.request.created",
        "workflow.request.submitted",
        "workflow.request.approved",
        "jit.grant.issued",
    ]


@pytest.mark.asyncio
async def test_workflow_rejects_illegal_state_transition() -> None:
    service, _audit, _revoker = build_workflow_service()
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1"},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="数据库故障排查",
        requested_ttl_seconds=1800,
        metadata={},
    )

    with pytest.raises(ValueError, match="INVALID_WORKFLOW_TRANSITION:draft->approved"):
        await service.approve_request(
            "wr-1",
            actor={
                "id": "approver-1",
                "username": "bob",
                "tenant_id": "tenant-1",
                "permissions": ["workflow:approve"],
            },
            decision_reason="不能跳过提交",
            grant_ttl_seconds=1800,
        )


@pytest.mark.asyncio
async def test_workflow_blocks_self_approval_in_service_layer() -> None:
    service, _audit, _revoker = build_workflow_service()
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1"},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="数据库故障排查",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")

    with pytest.raises(PermissionError, match="SELF_APPROVAL_NOT_ALLOWED"):
        await service.approve_request(
            "wr-1",
            actor={
                "id": "user-1",
                "username": "alice",
                "tenant_id": "tenant-1",
                "permissions": ["workflow:approve"],
            },
            decision_reason="自己审批自己",
            grant_ttl_seconds=1800,
        )


@pytest.mark.asyncio
async def test_workflow_revoke_approved_request_revokes_grant_and_sessions() -> None:
    service, audit, revoker = build_workflow_service()
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1"},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="数据库故障排查",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    await service.approve_request(
        "wr-1",
        actor={
            "id": "approver-1",
            "username": "bob",
            "tenant_id": "tenant-1",
            "permissions": ["workflow:approve"],
        },
        decision_reason="允许排障",
        grant_ttl_seconds=1800,
    )

    revoked = await service.revoke_request(
        "wr-1",
        actor={
            "id": "approver-1",
            "username": "bob",
            "tenant_id": "tenant-1",
            "permissions": ["workflow:approve"],
        },
        reason="risk_changed",
    )

    assert revoked.status is WorkflowRequestStatus.REVOKED
    assert revoker.revoked_grants == ["grant-1:jit_grant_revoked"]
    grant = await service.get_grant("grant-1", tenant_id="tenant-1")
    assert grant is not None
    assert grant.status == "revoked"
    assert [event["type"] for event in audit.events][-2:] == [
        "jit.grant.revoked",
        "workflow.request.revoked",
    ]


@pytest.mark.asyncio
async def test_workflow_single_use_grant_cannot_be_reused_for_sessions() -> None:
    service, audit, _revoker = build_workflow_service()
    await service.create_request(
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1"},
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        reason="数据库故障排查",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    await service.approve_request(
        "wr-1",
        actor={
            "id": "approver-1",
            "username": "bob",
            "tenant_id": "tenant-1",
            "permissions": ["workflow:approve"],
        },
        decision_reason="允许排障",
        grant_ttl_seconds=1800,
    )

    binding = await service.validate_for_session(
        jit_grant_id="grant-1",
        subject_id="user-1",
        tenant_id="tenant-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        now=datetime(2026, 6, 30, 12, 1, tzinfo=UTC),
    )
    await service.mark_session_bound(jit_grant_id=binding.jit_grant_id, session_id="session-1")

    grant = await service.get_grant("grant-1", tenant_id="tenant-1")
    assert grant is not None
    assert grant.status is JitGrantStatus.USED
    assert audit.events[-1]["type"] == "jit.grant.used"
    with pytest.raises(PermissionError, match="JIT_GRANT_NOT_ACTIVE:used"):
        await service.validate_for_session(
            jit_grant_id="grant-1",
            subject_id="user-1",
            tenant_id="tenant-1",
            asset_id="asset-1",
            account_id="root",
            protocol="ssh",
            action="session.connect",
            now=datetime(2026, 6, 30, 12, 2, tzinfo=UTC),
        )


def test_workflow_api_create_submit_approve_and_list_active_grants() -> None:
    service, _audit, _revoker = build_workflow_service()
    app.dependency_overrides[get_workflow_service] = lambda: service
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "tenant-1",
        "permissions": ["workflow:approve"],
    }
    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/workflows/requests",
                json={
                    "asset_id": "asset-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "reason": "数据库故障排查",
                    "requested_ttl_seconds": 1800,
                    "metadata": {"ticket_id": "INC-1001"},
                },
            )
            assert create_response.status_code == 201
            assert create_response.json()["status"] == "draft"

            submit_response = client.post("/api/v1/workflows/requests/wr-1/submit")
            assert submit_response.status_code == 200
            assert submit_response.json()["status"] == "pending"

            app.dependency_overrides[current_user] = lambda: {
                "id": "approver-1",
                "username": "bob",
                "tenant_id": "tenant-1",
                "permissions": ["workflow:approve"],
            }
            approve_response = client.post(
                "/api/v1/workflows/requests/wr-1/approve",
                json={
                    "decision_reason": "允许 30 分钟排障",
                    "grant_ttl_seconds": 1800,
                },
            )
            assert approve_response.status_code == 200
            assert approve_response.json()["grant_id"] == "grant-1"

            grants_response = client.get("/api/v1/workflows/grants/active")
            assert grants_response.status_code == 200
            assert grants_response.json()["items"][0]["id"] == "grant-1"
    finally:
        app.dependency_overrides.clear()


def test_workflow_api_detail_list_reject_and_revoke_paths() -> None:
    service, _audit, _revoker = build_workflow_service()
    app.dependency_overrides[get_workflow_service] = lambda: service
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "tenant-1",
        "permissions": ["workflow:approve"],
    }
    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/workflows/requests",
                json={
                    "asset_id": "asset-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "reason": "数据库故障排查",
                    "requested_ttl_seconds": 1800,
                },
            )
            assert create_response.status_code == 201
            detail_response = client.get("/api/v1/workflows/requests/wr-1")
            assert detail_response.status_code == 200
            list_response = client.get("/api/v1/workflows/requests")
            assert list_response.status_code == 200
            assert list_response.json()["total"] == 1

            submit_response = client.post("/api/v1/workflows/requests/wr-1/submit")
            assert submit_response.status_code == 200
            app.dependency_overrides[current_user] = lambda: {
                "id": "approver-1",
                "username": "bob",
                "tenant_id": "tenant-1",
                "permissions": ["workflow:approve"],
            }
            reject_response = client.post(
                "/api/v1/workflows/requests/wr-1/reject",
                json={"decision_reason": "窗口期不合适"},
            )
            assert reject_response.status_code == 200
            assert reject_response.json()["status"] == "rejected"

        service, _audit, _revoker = build_workflow_service()
        app.dependency_overrides[get_workflow_service] = lambda: service
        app.dependency_overrides[current_user] = lambda: {
            "id": "user-1",
            "username": "alice",
            "tenant_id": "tenant-1",
            "permissions": ["workflow:approve"],
        }
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/workflows/requests",
                json={
                    "asset_id": "asset-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "reason": "数据库故障排查",
                    "requested_ttl_seconds": 1800,
                },
            ).status_code == 201
            assert client.post("/api/v1/workflows/requests/wr-1/submit").status_code == 200
            revoke_response = client.post(
                "/api/v1/workflows/requests/wr-1/revoke",
                json={"reason": "用户取消"},
            )
            assert revoke_response.status_code == 200
            assert revoke_response.json()["status"] == "revoked"
    finally:
        app.dependency_overrides.clear()
