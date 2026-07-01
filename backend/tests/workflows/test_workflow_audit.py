from fastapi.testclient import TestClient

from app.api.audits.schemas import AuditEvent
from app.api.audits.service import audit_service
from app.core.deps import current_user
from app.main import app
from app.workflows.audit import WORKFLOW_AUDIT_EVENTS, WorkflowAuditSink, emit_workflow_audit_event


def _system_actor() -> dict[str, object]:
    return {
        "id": "system",
        "username": "system",
        "tenant_id": "tenant-a",
        "permissions": ["audit:write", "audit:read"],
    }


def _audit_user() -> dict[str, object]:
    return {
        "id": "auditor-1",
        "username": "auditor",
        "tenant_id": "tenant-a",
        "permissions": ["audit:read"],
    }


def test_workflow_audit_event_catalog_covers_phase2_state_changes():
    expected_events = {
        "workflow.request.created",
        "workflow.request.submitted",
        "workflow.request.approved",
        "workflow.request.rejected",
        "workflow.request.revoked",
        "workflow.request.expired",
        "jit.grant.issued",
        "jit.grant.used",
        "jit.grant.expired",
        "jit.grant.revoked",
        "session.connection_token.issued",
        "session.revoked_by_jit_grant",
    }
    assert expected_events == WORKFLOW_AUDIT_EVENTS


def test_emit_workflow_audit_event_redacts_metadata_and_reaches_siem_payload():
    delivered_events: list[AuditEvent] = []

    class CaptureSiemClient:
        async def deliver(self, event: AuditEvent) -> None:
            delivered_events.append(event)

    original_siem_client = audit_service._siem_client
    audit_service._siem_client = CaptureSiemClient()
    try:
        event = emit_workflow_audit_event(
            audit_service,
            actor=_system_actor(),
            event_type="workflow.request.approved",
            workflow_request_id="wr_1",
            jit_grant_id="jg_1",
            action="approve",
            resource_type="workflow_request",
            resource_id="wr_1",
            metadata={
                "authorization": "Bearer secret-token",
                "cookie": "session=secret-cookie",
                "credential": "secret-credential",
                "decision_reason": "safe reason",
            },
        )
    finally:
        audit_service._siem_client = original_siem_client

    assert event.event_type == "workflow.request.approved"
    assert event.category == "workflow"
    assert event.metadata["workflow_request_id"] == "wr_1"
    assert event.metadata["jit_grant_id"] == "jg_1"
    assert event.metadata["authorization"] == "***REDACTED***"
    assert event.metadata["cookie"] == "***REDACTED***"
    assert event.metadata["credential"] == "***REDACTED***"
    assert event.metadata["decision_reason"] == "safe reason"
    assert delivered_events[0].metadata == event.metadata
    assert "secret-token" not in str(event.metadata)
    assert "secret-cookie" not in str(delivered_events[0].metadata)
    assert "secret-credential" not in str(delivered_events[0].metadata)


def test_workflow_audit_events_are_queryable_through_audit_api():
    emit_workflow_audit_event(
        audit_service,
        actor=_system_actor(),
        event_type="jit.grant.revoked",
        workflow_request_id="wr_2",
        jit_grant_id="jg_2",
        action="revoke",
        resource_type="jit_grant",
        resource_id="jg_2",
        metadata={"reason": "manual revoke"},
    )

    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)
    response = client.get("/api/v1/audits/events?event_type=jit.grant.revoked")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["event_type"] == "jit.grant.revoked"
    assert items[0]["metadata"]["workflow_request_id"] == "wr_2"
    assert items[0]["metadata"]["jit_grant_id"] == "jg_2"


def test_session_revoked_by_jit_grant_event_reaches_siem_payload():
    delivered_events: list[AuditEvent] = []

    class CaptureSiemClient:
        async def deliver(self, event: AuditEvent) -> None:
            delivered_events.append(event)

    original_siem_client = audit_service._siem_client
    audit_service._siem_client = CaptureSiemClient()
    try:
        event = emit_workflow_audit_event(
            audit_service,
            actor=_system_actor(),
            event_type="session.revoked_by_jit_grant",
            workflow_request_id="wr_3",
            jit_grant_id="jg_3",
            action="disconnect",
            resource_type="session",
            resource_id="session-1",
            metadata={"session_id": "session-1", "cookie": "plain-cookie"},
        )
    finally:
        audit_service._siem_client = original_siem_client

    assert event.event_type == "session.revoked_by_jit_grant"
    assert event.metadata["workflow_request_id"] == "wr_3"
    assert event.metadata["jit_grant_id"] == "jg_3"
    assert event.metadata["session_id"] == "session-1"
    assert event.metadata["cookie"] == "***REDACTED***"
    assert delivered_events[0].metadata == event.metadata
    assert "plain-cookie" not in str(delivered_events[0].metadata)


async def test_workflow_audit_sink_bridges_service_events_to_audit_service():
    delivered_events: list[AuditEvent] = []

    class CaptureSiemClient:
        async def deliver(self, event: AuditEvent) -> None:
            delivered_events.append(event)

    original_siem_client = audit_service._siem_client
    audit_service._siem_client = CaptureSiemClient()
    sink = WorkflowAuditSink(audit_service)
    try:
        await sink.publish(
            {
                "type": "workflow.request.approved",
                "workflow_request_id": "wr_sink",
                "jit_grant_id": "grant_sink",
                "tenant_id": "tenant-a",
                "requester_id": "user-1",
                "asset_id": "asset-1",
                "account_id": "root",
                "protocol": "ssh",
                "action": "session.connect",
                "status": "approved",
                "decision_reason": "approved",
            }
        )
        ignored = await sink.publish({"type": "session.active", "tenant_id": "tenant-a"})
    finally:
        audit_service._siem_client = original_siem_client

    assert ignored is None
    assert delivered_events[-1].event_type == "workflow.request.approved"
    assert delivered_events[-1].metadata["workflow_request_id"] == "wr_sink"
    assert delivered_events[-1].metadata["jit_grant_id"] == "grant_sink"


async def test_connection_token_issued_event_reaches_audit_without_plain_token():
    delivered_events: list[AuditEvent] = []

    class CaptureSiemClient:
        async def deliver(self, event: AuditEvent) -> None:
            delivered_events.append(event)

    original_siem_client = audit_service._siem_client
    audit_service._siem_client = CaptureSiemClient()
    sink = WorkflowAuditSink(audit_service)
    try:
        await sink.publish(
            {
                "type": "session.connection_token.issued",
                "tenant_id": "tenant-a",
                "subject_id": "user-1",
                "workflow_request_id": "wr_token",
                "jit_grant_id": "grant_token",
                "asset_id": "asset-1",
                "account_id": "root",
                "protocol": "ssh",
                "action": "session.connect",
                "expires_at": "2026-07-01T12:25:00+00:00",
            }
        )
    finally:
        audit_service._siem_client = original_siem_client

    event = delivered_events[-1]
    assert event.event_type == "session.connection_token.issued"
    assert event.resource_type == "jit_grant"
    assert event.resource_id == "grant_token"
    assert event.metadata["workflow_request_id"] == "wr_token"
    assert event.metadata["jit_grant_id"] == "grant_token"
    assert "connection_token" not in event.metadata
