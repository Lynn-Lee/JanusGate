from fastapi.testclient import TestClient

from app.api.audits.schemas import AuditEvent
from app.api.audits.service import audit_service
from app.core.deps import current_user
from app.main import app


def _audit_user() -> dict:
    return {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "permissions": ["audit:write", "audit:read"],
    }


def _read_only_user() -> dict:
    return {
        "id": "user-2",
        "username": "bob",
        "tenant_id": "tenant-a",
        "permissions": ["audit:read"],
    }


def test_create_audit_event_persists_and_masks_sensitive_fields():
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    response = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "command.executed",
            "action": "run_command",
            "resource_type": "asset",
            "resource_id": "asset-1",
            "session_id": "session-1",
            "severity": "medium",
            "message": "operator ran command",
            "metadata": {"command": "cat /etc/passwd", "password": "secret-token"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["tenant_id"] == "tenant-a"
    assert body["actor_id"] == "user-1"
    assert body["metadata"]["command"] == "cat /etc/passwd"
    assert body["metadata"]["password"] == "***REDACTED***"

    list_response = client.get("/api/v1/audits/events?event_type=command.executed")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert any(item["id"] == body["id"] for item in items)


def test_siem_delivery_failure_does_not_block_audit_event_creation():
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    response = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "session.started",
            "action": "start_session",
            "resource_type": "asset",
            "resource_id": "asset-2",
            "severity": "low",
            "message": "session started",
            "metadata": {"force_siem_failure": True},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["siem_delivery_status"] == "failed"
    assert body["siem_delivery_error"]
    assert body["siem_delivery_attempts"] == 1
    assert body["siem_next_retry_at"]


def test_write_permission_required_for_audit_event_creation():
    app.dependency_overrides[current_user] = _read_only_user
    client = TestClient(app)

    response = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "command.executed",
            "action": "run_command",
            "resource_type": "asset",
            "resource_id": "asset-1",
        },
    )

    assert response.status_code == 403


def test_audit_events_are_append_only_with_hash_chain_and_supported_categories():
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    first = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "vault.secret.read",
            "category": "vault",
            "action": "read_secret",
            "resource_type": "credential",
            "resource_id": "cred-1",
            "metadata": {"nested": {"access_token": "plain-token"}},
        },
    ).json()
    second = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "workflow.approved",
            "category": "workflow",
            "action": "approve_jit",
            "resource_type": "workflow_request",
            "resource_id": "wf-1",
        },
    ).json()

    assert first["category"] == "vault"
    assert first["sequence_number"] == 1
    assert first["previous_event_hash"] is None
    assert first["event_hash"]
    assert first["metadata"]["nested"]["access_token"] == "***REDACTED***"
    assert second["category"] == "workflow"
    assert second["sequence_number"] == 2
    assert second["previous_event_hash"] == first["event_hash"]
    assert second["event_hash"] != first["event_hash"]


def test_unknown_audit_category_is_rejected():
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    response = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "unknown.event",
            "category": "unknown",
            "action": "do_unknown",
            "resource_type": "asset",
            "resource_id": "asset-1",
        },
    )

    assert response.status_code == 422


def test_sensitive_metadata_redaction_covers_headers_credentials_and_siem_payload():
    delivered_events: list[AuditEvent] = []

    class CaptureSiemClient:
        async def deliver(self, event: AuditEvent) -> None:
            delivered_events.append(event)

    original_siem_client = audit_service._siem_client
    audit_service._siem_client = CaptureSiemClient()
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    try:
        response = client.post(
            "/api/v1/audits/events",
            json={
                "event_type": "connector.auth.failed",
                "category": "connector",
                "action": "authenticate",
                "resource_type": "connector",
                "resource_id": "connector-1",
                "metadata": {
                    "authorization": "Bearer plain-token",
                    "cookie": "sessionid=plain-cookie",
                    "credential": "plain-credential",
                    "credentials": {"ssh_key": "plain-key", "username": "safe-user"},
                    "headers": {
                        "Authorization": "Bearer nested-token",
                        "X-Request-ID": "req-123",
                    },
                },
            },
        )
    finally:
        audit_service._siem_client = original_siem_client

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"]["authorization"] == "***REDACTED***"
    assert body["metadata"]["cookie"] == "***REDACTED***"
    assert body["metadata"]["credential"] == "***REDACTED***"
    assert body["metadata"]["credentials"] == "***REDACTED***"
    assert body["metadata"]["headers"]["Authorization"] == "***REDACTED***"
    assert body["metadata"]["headers"]["X-Request-ID"] == "req-123"

    list_body = client.get("/api/v1/audits/events?event_type=connector.auth.failed").json()
    item = list_body["items"][0]
    assert item["metadata"] == body["metadata"]
    assert delivered_events[0].metadata == body["metadata"]

    serialized = str(body) + str(item) + str(delivered_events[0].metadata)
    assert "plain-token" not in serialized
    assert "plain-cookie" not in serialized
    assert "plain-credential" not in serialized
    assert "plain-key" not in serialized


def test_audit_report_summary_counts_current_tenant_without_metadata_leak():
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "session.started",
            "category": "session",
            "action": "start_session",
            "resource_type": "asset",
            "resource_id": "asset-1",
            "severity": "high",
            "metadata": {"token": "raw-session-token"},
        },
    )
    client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "connector.auth.failed",
            "category": "connector",
            "action": "authenticate",
            "resource_type": "connector",
            "resource_id": "connector-1",
            "severity": "critical",
            "metadata": {"force_siem_failure": True, "password": "raw-password"},
        },
    )

    app.dependency_overrides[current_user] = lambda: {
        **_audit_user(),
        "tenant_id": "tenant-b",
    }
    client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "session.started",
            "category": "session",
            "action": "start_session",
            "resource_type": "asset",
            "resource_id": "asset-2",
            "severity": "low",
        },
    )

    app.dependency_overrides[current_user] = _audit_user
    response = client.get("/api/v1/audits/reports/summary")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "tenant_id": "tenant-a",
        "total": 2,
        "high_or_critical_total": 2,
        "by_severity": {"high": 1, "critical": 1},
        "by_category": {"session": 1, "connector": 1},
        "by_siem_delivery_status": {"delivered": 1, "failed": 1},
    }
    assert "raw-session-token" not in str(body)
    assert "raw-password" not in str(body)


def test_compliance_report_export_returns_signed_tenant_scoped_event_hashes_without_details():
    app.dependency_overrides[current_user] = _audit_user
    client = TestClient(app)

    first = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "session.started",
            "category": "session",
            "action": "start_session",
            "resource_type": "asset",
            "resource_id": "asset-1",
            "severity": "high",
            "message": "operator session started",
            "metadata": {"token": "raw-session-token"},
        },
    ).json()
    second = client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "workflow.approved",
            "category": "workflow",
            "action": "approve_jit",
            "resource_type": "workflow_request",
            "resource_id": "wf-1",
            "severity": "medium",
            "metadata": {"password": "raw-password"},
        },
    ).json()

    app.dependency_overrides[current_user] = lambda: {
        **_audit_user(),
        "tenant_id": "tenant-b",
    }
    client.post(
        "/api/v1/audits/events",
        json={
            "event_type": "session.started",
            "category": "session",
            "action": "start_session",
            "resource_type": "asset",
            "resource_id": "asset-2",
        },
    )

    app.dependency_overrides[current_user] = _audit_user
    response = client.get("/api/v1/audits/reports/compliance?template=soc2-access")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["template"] == "soc2-access"
    assert body["total"] == 2
    assert body["event_ids"] == [first["id"], second["id"]]
    assert body["hash_chain_start"] == first["event_hash"]
    assert body["hash_chain_end"] == second["event_hash"]
    assert body["report_signature"]
    assert body["generated_at"]
    assert body["period_start"] == first["created_at"]
    assert body["period_end"] == second["created_at"]
    assert "metadata" not in body
    assert "message" not in body
    assert "resource_id" not in body
    assert "session_id" not in body

    serialized = str(body)
    assert "raw-session-token" not in serialized
    assert "raw-password" not in serialized
