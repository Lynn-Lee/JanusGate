from fastapi.testclient import TestClient

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
