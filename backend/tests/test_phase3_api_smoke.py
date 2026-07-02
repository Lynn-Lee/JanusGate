"""Phase 3 API-level smoke for the MVP JIT-to-session chain."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.audits.service import audit_service
from app.api.sessions.routes import get_session_gateway_service
from app.api.sessions.service import (
    InMemoryConnectionTokenStore,
    InMemorySessionStore,
    PolicyDecisionServiceClient,
    SessionGatewayService,
)
from app.api.workflows.routes import get_workflow_service
from app.api.workflows.service import InMemoryWorkflowStore, WorkflowService
from app.core.deps import current_user
from app.main import app
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import PolicyRule
from app.workflows.audit import WorkflowAuditSink


def test_phase3_api_smoke_runs_jit_session_revoke_audit_chain() -> None:
    now = datetime.now(UTC)
    audit_sink = WorkflowAuditSink(audit_service)
    workflow_service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=audit_sink,
        now=lambda: now,
        request_id_factory=lambda: "wr-smoke-1",
        grant_id_factory=lambda: "grant-smoke-1",
    )
    session_service = SessionGatewayService(
        policy_client=PolicyDecisionServiceClient(
            PolicyDecisionService(
                rules=[
                    PolicyRule(
                        id="approved-jit-session-smoke",
                        subject_ids=["*"],
                        actions=["session.connect"],
                        resource_ids=["*"],
                        tenant_id="*",
                        require_approval=True,
                    )
                ]
            )
        ),
        token_store=InMemoryConnectionTokenStore(token_id_factory=lambda: "raw-smoke-token"),
        session_store=InMemorySessionStore(),
        audit_sink=audit_sink,
        jit_grant_client=workflow_service,
        now=lambda: now,
        session_id_factory=lambda: "session-smoke-1",
    )
    workflow_service.session_revoker = session_service

    def requester_user() -> dict[str, object]:
        return {
            "id": "user-smoke-1",
            "username": "alice",
            "tenant_id": "tenant-smoke-1",
            "permissions": ["sessions:connect", "audit:read"],
        }

    app.dependency_overrides[get_workflow_service] = lambda: workflow_service
    app.dependency_overrides[get_session_gateway_service] = lambda: session_service
    app.dependency_overrides[current_user] = requester_user
    try:
        with TestClient(app, client=("198.51.100.30", 50000)) as client:
            create = client.post(
                "/api/v1/workflows/requests",
                json={
                    "asset_id": "asset-smoke-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "reason": "Phase 3 smoke",
                    "requested_ttl_seconds": 1800,
                    "metadata": {"ticket_id": "SMOKE-1"},
                },
            )
            assert create.status_code == 201
            assert client.post("/api/v1/workflows/requests/wr-smoke-1/submit").status_code == 200

            app.dependency_overrides[current_user] = lambda: {
                "id": "approver-smoke-1",
                "username": "bob",
                "tenant_id": "tenant-smoke-1",
                "permissions": ["workflow:approve", "audit:read"],
            }
            approve = client.post(
                "/api/v1/workflows/requests/wr-smoke-1/approve",
                json={"decision_reason": "允许 smoke", "grant_ttl_seconds": 1800},
            )
            assert approve.status_code == 200
            assert approve.json()["grant_id"] == "grant-smoke-1"

            app.dependency_overrides[current_user] = requester_user
            token = client.post(
                "/api/v1/sessions/connection-token",
                json={
                    "jit_grant_id": "grant-smoke-1",
                    "asset_id": "asset-smoke-1",
                    "account_id": "root",
                    "protocol": "ssh",
                },
            )
            assert token.status_code == 201
            raw_token = token.json()["connection_token"]
            assert raw_token == "raw-smoke-token"

            session = client.post(
                "/api/v1/sessions/",
                json={
                    "asset_id": "asset-smoke-1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "jit_grant_id": "grant-smoke-1",
                    "connection_token": raw_token,
                },
            )
            assert session.status_code == 201, session.text
            assert session.json()["id"] == "session-smoke-1"
            assert session.json()["status"] == "active"

            app.dependency_overrides[current_user] = lambda: {
                "id": "approver-smoke-1",
                "username": "bob",
                "tenant_id": "tenant-smoke-1",
                "permissions": ["workflow:approve", "audit:read"],
            }
            revoke = client.post(
                "/api/v1/workflows/requests/wr-smoke-1/revoke",
                json={"reason": "smoke complete"},
            )
            assert revoke.status_code == 200
            assert revoke.json()["status"] == "revoked"

            app.dependency_overrides[current_user] = requester_user
            sessions = client.get("/api/v1/sessions/")
            assert sessions.status_code == 200
            assert sessions.json()["items"][0]["status"] == "closed"

            audits = client.get("/api/v1/audits/events?limit=100")
            assert audits.status_code == 200
            event_types = [item["event_type"] for item in audits.json()["items"]]
            assert "workflow.request.approved" in event_types
            assert "session.connection_token.issued" in event_types
            assert "session.revoked_by_jit_grant" in event_types
            assert raw_token not in str(audits.json())
    finally:
        app.dependency_overrides.clear()
