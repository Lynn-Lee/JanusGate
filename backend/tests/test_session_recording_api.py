"""Phase 4 session recording and command search API contract tests."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.session_recordings import _build_command_search_filter
from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.session_recording import SessionCommandEvent


def test_session_command_events_define_full_text_search_indexes() -> None:
    indexes = {index.name: index for index in SessionCommandEvent.__table__.indexes}

    assert "ix_session_command_events_tenant_occurred_id" in indexes

    search_index = indexes["ix_session_command_events_search_vector"]
    assert search_index.dialect_options["postgresql"]["using"] == "gin"
    assert search_index._ddl_if is not None
    assert search_index._ddl_if.dialect == "postgresql"
    assert "to_tsvector" in str(search_index.expressions[0])


def test_session_command_search_uses_postgresql_full_text_predicate() -> None:
    predicate = _build_command_search_filter(query="nginx restart", dialect_name="postgresql")

    compiled = str(predicate)
    assert "to_tsvector" in compiled
    assert "plainto_tsquery" in compiled


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def install_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


def install_user(*, tenant_id: str, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.mark.asyncio
async def test_session_recording_api_creates_command_events_and_searches_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        assert recording_response.status_code == 201
        recording = recording_response.json()
        command_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "sudo systemctl restart nginx",
                "exit_code": 0,
                "output_excerpt": "token=raw-secret should be redacted",
            },
        )
        tenant_a_search = client.get("/api/v1/session-recordings/commands?query=nginx")

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_search = client.get("/api/v1/session-recordings/commands?query=nginx")
        tenant_b_append = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 2,
                "command": "cat /etc/passwd",
                "exit_code": 0,
            },
        )

    assert recording["tenant_id"] == "tenant-a"
    assert recording["session_id"] == "session-a"
    assert recording["status"] == "recording"
    assert recording["storage_uri"] == "s3://janusgate-recordings/tenant-a/session-a.cast"

    assert command_response.status_code == 201
    command = command_response.json()
    assert command["recording_id"] == recording["id"]
    assert command["command"] == "sudo systemctl restart nginx"
    assert "raw-secret" not in command["output_excerpt"]

    assert tenant_a_search.status_code == 200
    assert tenant_a_search.json()["total"] == 1
    assert tenant_a_search.json()["items"][0]["session_id"] == "session-a"
    assert tenant_a_search.json()["items"][0]["command"] == "sudo systemctl restart nginx"

    assert tenant_b_search.status_code == 200
    assert tenant_b_search.json() == {"items": [], "total": 0}
    assert tenant_b_append.status_code == 404
    assert tenant_b_append.json()["code"] == "SESSION_RECORDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_recording_api_closes_recordings_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        recording = recording_response.json()
        close_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/close"
        )
        second_close_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/close"
        )

        tenant_a_second_recording_response = client.post(
            "/api/v1/sessions/session-b/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-b.cast",
            },
        )
        tenant_a_second_recording = tenant_a_second_recording_response.json()

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_close_response = client.post(
            f"/api/v1/session-recordings/{tenant_a_second_recording['id']}/close"
        )

    assert close_response.status_code == 200
    closed_recording = close_response.json()
    assert closed_recording["status"] == "closed"
    assert closed_recording["ended_at"] is not None

    assert second_close_response.status_code == 404
    assert second_close_response.json()["code"] == "SESSION_RECORDING_NOT_FOUND"

    assert tenant_b_close_response.status_code == 404
    assert tenant_b_close_response.json()["code"] == "SESSION_RECORDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_recording_api_lists_recording_commands_for_playback_by_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        recording = recording_response.json()
        client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={"sequence": 2, "command": "tail -f /var/log/syslog"},
        )
        client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={"sequence": 1, "command": "whoami"},
        )

        tenant_a_timeline = client.get(
            f"/api/v1/session-recordings/{recording['id']}/commands"
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b_timeline = client.get(
            f"/api/v1/session-recordings/{recording['id']}/commands"
        )

    assert tenant_a_timeline.status_code == 200
    assert tenant_a_timeline.json()["total"] == 2
    assert [item["sequence"] for item in tenant_a_timeline.json()["items"]] == [1, 2]
    assert [item["command"] for item in tenant_a_timeline.json()["items"]] == [
        "whoami",
        "tail -f /var/log/syslog",
    ]

    assert tenant_b_timeline.status_code == 404
    assert tenant_b_timeline.json()["code"] == "SESSION_RECORDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_connector_session_recording_ingest_requires_active_same_tenant_connector(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        active_connector_response = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-a",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-a",
                "capabilities": ["ssh"],
            },
        )
        inactive_connector_response = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-inactive",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-inactive",
                "capabilities": ["ssh"],
                "status": "inactive",
            },
        )
        recording_response = client.post(
            "/api/v1/sessions/session-a/recordings",
            json={
                "asset_id": "asset-1",
                "account_id": "account-1",
                "protocol": "ssh",
                "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
            },
        )
        active_connector = active_connector_response.json()
        inactive_connector = inactive_connector_response.json()
        recording = recording_response.json()

        ingest_response = client.post(
            f"/api/v1/connectors/{active_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "sudo systemctl restart nginx",
                "exit_code": 0,
                "output_excerpt": "password=raw-secret",
            },
        )
        inactive_ingest_response = client.post(
            f"/api/v1/connectors/{inactive_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 2, "command": "whoami"},
        )
        close_response = client.post(
            f"/api/v1/session-recordings/{recording['id']}/close"
        )
        closed_ingest_response = client.post(
            f"/api/v1/connectors/{active_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 3, "command": "id"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        cross_tenant_ingest_response = client.post(
            f"/api/v1/connectors/{active_connector['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 4, "command": "hostname"},
        )

    assert ingest_response.status_code == 201
    command = ingest_response.json()
    assert command["recording_id"] == recording["id"]
    assert command["session_id"] == "session-a"
    assert command["command"] == "sudo systemctl restart nginx"
    assert "raw-secret" not in command["output_excerpt"]

    assert inactive_ingest_response.status_code == 403
    assert inactive_ingest_response.json()["code"] == "CONNECTOR_NOT_ACTIVE"

    assert close_response.status_code == 200
    assert closed_ingest_response.status_code == 404
    assert closed_ingest_response.json()["code"] == "SESSION_RECORDING_NOT_FOUND"

    assert cross_tenant_ingest_response.status_code == 404
    assert cross_tenant_ingest_response.json()["code"] == "CONNECTOR_NOT_FOUND"


def _create_recording(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/sessions/session-a/recordings",
        json={
            "asset_id": "asset-1",
            "account_id": "account-1",
            "protocol": "ssh",
            "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_command_event_allowed_by_default_when_no_acl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording = _create_recording(client)
        allowed = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={"sequence": 1, "command": "ls -la", "exit_code": 0, "output_excerpt": "ok"},
        )
        timeline = client.get(f"/api/v1/session-recordings/{recording['id']}/commands")

    assert allowed.status_code == 201
    assert allowed.json()["command"] == "ls -la"
    assert timeline.json()["total"] == 1
    assert timeline.json()["items"][0]["command"] == "ls -la"


@pytest.mark.asyncio
async def test_rejected_command_is_blocked_not_persisted_and_audited(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    blocked_command = "sudo rm -rf /secret-payload"

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
        recording = _create_recording(client)
        created_acl = client.post(
            "/api/v1/command-filter-acls/",
            json={
                "name": "deny-rm",
                "priority": 10,
                "action": "reject",
                "command_groups": [
                    {"name": "danger", "match_type": "command", "patterns": ["rm"]}
                ],
            },
        )
        rejected = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": blocked_command,
                "exit_code": 0,
                "output_excerpt": "deleted",
            },
        )
        timeline = client.get(f"/api/v1/session-recordings/{recording['id']}/commands")
        audits = client.get("/api/v1/audits/events?event_type=session.command.rejected")

    assert created_acl.status_code == 201
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "COMMAND_REJECT"
    assert timeline.status_code == 200
    assert timeline.json() == {"items": [], "total": 0}
    assert audits.status_code == 200
    assert audits.json()["total"] == 1
    event = audits.json()["items"][0]
    assert event["event_type"] == "session.command.rejected"
    assert event["metadata"]["reason_code"] == "COMMAND_REJECT"
    assert event["metadata"]["matched_acl_id"] == created_acl.json()["id"]
    assert "command_sha256" in event["metadata"]
    assert blocked_command not in str(event)
    assert "secret-payload" not in str(event)
    assert "command" not in event["metadata"]


@pytest.mark.asyncio
async def test_output_excerpt_masking_is_applied_cumulatively_on_persist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording = _create_recording(client)
        ssn_rule = client.post(
            "/api/v1/data-masking-rules/",
            json={
                "name": "ssn",
                "priority": 10,
                "match_type": "regex",
                "patterns": [r"\d{3}-\d{2}-\d{4}"],
                "placeholder": "[SSN]",
            },
        )
        email_rule = client.post(
            "/api/v1/data-masking-rules/",
            json={
                "name": "email",
                "priority": 20,
                "match_type": "regex",
                "patterns": [r"\w+@\w+\.\w+"],
                "placeholder": "[EMAIL]",
            },
        )
        stored = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "cat dump.txt",
                "exit_code": 0,
                "output_excerpt": "ssn 123-45-6789 mail bob@acme.com password=raw-secret",
            },
        )

    assert ssn_rule.status_code == 201
    assert email_rule.status_code == 201
    assert stored.status_code == 201
    excerpt = stored.json()["output_excerpt"]
    assert excerpt == "ssn [SSN] mail [EMAIL] password=[REDACTED]"
    assert "123-45-6789" not in excerpt
    assert "bob@acme.com" not in excerpt
    assert "raw-secret" not in excerpt


@pytest.mark.asyncio
async def test_connector_ingest_blocks_reject_and_masks_output(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        connector = client.post(
            "/api/v1/connectors/",
            json={
                "name": "edge-a",
                "environment": "prod",
                "public_key_fingerprint": "sha256:connector-a",
                "capabilities": ["ssh"],
            },
        )
        recording = _create_recording(client)
        client.post(
            "/api/v1/command-filter-acls/",
            json={
                "name": "deny-rm",
                "action": "reject",
                "command_groups": [
                    {"name": "danger", "match_type": "command", "patterns": ["rm"]}
                ],
            },
        )
        client.post(
            "/api/v1/data-masking-rules/",
            json={
                "name": "ssn",
                "match_type": "regex",
                "patterns": [r"\d{3}-\d{2}-\d{4}"],
                "placeholder": "[SSN]",
            },
        )
        rejected = client.post(
            f"/api/v1/connectors/{connector.json()['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={"sequence": 1, "command": "rm -rf /", "output_excerpt": "gone"},
        )
        allowed = client.post(
            f"/api/v1/connectors/{connector.json()['id']}"
            f"/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 2,
                "command": "cat notes",
                "output_excerpt": "ssn 123-45-6789",
            },
        )
        timeline = client.get(f"/api/v1/session-recordings/{recording['id']}/commands")

    assert connector.status_code == 201
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "COMMAND_REJECT"
    assert allowed.status_code == 201
    assert allowed.json()["output_excerpt"] == "ssn [SSN]"
    assert timeline.json()["total"] == 1
    assert timeline.json()["items"][0]["command"] == "cat notes"


@pytest.mark.asyncio
async def test_other_tenant_acl_does_not_block_this_tenant_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-b", permissions=["admin"])
        foreign_acl = client.post(
            "/api/v1/command-filter-acls/",
            json={
                "name": "deny-rm-b",
                "action": "reject",
                "command_groups": [
                    {"name": "danger", "match_type": "command", "patterns": ["rm"]}
                ],
            },
        )

        install_user(tenant_id="tenant-a", permissions=["admin"])
        recording = _create_recording(client)
        allowed = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={"sequence": 1, "command": "rm -rf /data", "exit_code": 0},
        )

    assert foreign_acl.status_code == 201
    assert allowed.status_code == 201
    assert allowed.json()["command"] == "rm -rf /data"



@pytest.mark.asyncio
async def test_persist_evaluate_failure_is_fail_closed_and_audited(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_db(session_factory)

    def boom(self, request):  # noqa: ANN001
        raise RuntimeError("evaluator crashed")

    from app.policy.decision import PolicyDecisionService

    monkeypatch.setattr(PolicyDecisionService, "evaluate_command", boom)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
        recording = _create_recording(client)
        rejected = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "id",
                "exit_code": 0,
                "output_excerpt": "uid=0",
            },
        )
        timeline = client.get(f"/api/v1/session-recordings/{recording['id']}/commands")
        audits = client.get("/api/v1/audits/events?event_type=session.command.rejected")

    assert rejected.status_code == 403
    assert rejected.json()["code"] == "COMMAND_EVALUATE_FAILED"
    assert timeline.json() == {"items": [], "total": 0}
    assert audits.json()["total"] == 1
    assert audits.json()["items"][0]["metadata"]["reason_code"] == "COMMAND_EVALUATE_FAILED"


@pytest.mark.asyncio
async def test_persist_review_is_blocked_not_stored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin", "audit:read"])
        recording = _create_recording(client)
        created_acl = client.post(
            "/api/v1/command-filter-acls/",
            json={
                "name": "review-rm",
                "priority": 10,
                "action": "review",
                "command_groups": [
                    {"name": "danger", "match_type": "command", "patterns": ["rm"]}
                ],
            },
        )
        rejected = client.post(
            f"/api/v1/session-recordings/{recording['id']}/commands",
            json={
                "sequence": 1,
                "command": "rm -rf /tmp",
                "exit_code": 0,
                "output_excerpt": "gone",
            },
        )
        timeline = client.get(f"/api/v1/session-recordings/{recording['id']}/commands")
        audits = client.get("/api/v1/audits/events?event_type=session.command.rejected")

    assert created_acl.status_code == 201
    assert rejected.status_code == 403
    assert timeline.json() == {"items": [], "total": 0}
    assert audits.json()["total"] == 1
