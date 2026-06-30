"""Session Gateway lifecycle and API tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.sessions.routes import get_session_gateway_service
from app.api.sessions.service import (
    ConnectionToken,
    InMemorySessionStore,
    SessionGatewayService,
    SessionStatus,
)
from app.core.deps import current_user
from app.main import app


class FakePolicyClient:
    def __init__(self, decision: str = "allow") -> None:
        self.decision = decision
        self.requests: list[dict] = []

    async def evaluate(self, request: dict) -> dict:
        self.requests.append(request)
        return {
            "decision": self.decision,
            "reason_code": "EXPLICIT_ALLOW" if self.decision == "allow" else "POLICY_DENY",
            "explain": ["fake-policy"],
            "ttl_seconds": 300,
            "obligations": [],
        }


class FakeTokenStore:
    def __init__(self, token: ConnectionToken) -> None:
        self.token = token
        self.consumed = False

    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        assert token_id == self.token.token_id
        self.consumed = True
        return self.token


class FakeConnectorScheduler:
    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def dispatch(self, session_id: str, connector_id: str) -> dict:
        self.dispatched.append(session_id)
        return {
            "connector_session_id": f"connector-{session_id}",
            "connection_url": f"wss://connector.example/sessions/{session_id}",
        }


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


def build_service(
    *,
    policy_decision: str = "allow",
    expires_at: datetime | None = None,
) -> tuple[SessionGatewayService, FakePolicyClient, FakeTokenStore, FakeAuditSink]:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    token = ConnectionToken(
        token_id="token-1",
        subject_id="user-1",
        asset_id="asset-1",
        account_id="account-1",
        connector_id="connector-1",
        expires_at=expires_at or now + timedelta(minutes=5),
    )
    policy = FakePolicyClient(policy_decision)
    token_store = FakeTokenStore(token)
    audit = FakeAuditSink()
    service = SessionGatewayService(
        policy_client=policy,
        token_store=token_store,
        connector_scheduler=FakeConnectorScheduler(),
        session_store=InMemorySessionStore(),
        audit_sink=audit,
        now=lambda: now,
    )
    return service, policy, token_store, audit


@pytest.mark.asyncio
async def test_create_session_requires_policy_allow_and_short_lived_token() -> None:
    service, policy, token_store, audit = build_service()

    session = await service.create_session(
        subject_id="user-1",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token="token-1",
        client_ip="203.0.113.10",
    )

    assert session.status is SessionStatus.ACTIVE
    assert session.asset_id == "asset-1"
    assert session.account_id == "account-1"
    assert session.connector_id == "connector-1"
    assert session.connection_url == f"wss://connector.example/sessions/{session.id}"
    assert "credential" not in session.model_dump()
    assert token_store.consumed is True
    assert policy.requests[0]["action"] == "session.connect"
    assert [event["type"] for event in audit.events] == [
        "session.requested",
        "session.authorized",
        "session.connecting",
        "session.active",
    ]


@pytest.mark.asyncio
async def test_create_session_denies_before_consuming_token_when_policy_denies() -> None:
    service, _policy, token_store, audit = build_service(policy_decision="deny")

    with pytest.raises(PermissionError, match="POLICY_DENY"):
        await service.create_session(
            subject_id="user-1",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            connection_token="token-1",
            client_ip="203.0.113.10",
        )

    assert token_store.consumed is False
    assert audit.events[-1]["type"] == "session.denied"


@pytest.mark.asyncio
async def test_create_session_rejects_expired_connection_token() -> None:
    service, _policy, _token_store, audit = build_service(
        expires_at=datetime(2026, 6, 29, 14, 59, tzinfo=UTC)
    )

    with pytest.raises(ValueError, match="CONNECTION_TOKEN_EXPIRED"):
        await service.create_session(
            subject_id="user-1",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            connection_token="token-1",
            client_ip="203.0.113.10",
        )

    assert audit.events[-1]["type"] == "session.failed"
    assert audit.events[-1]["reason_code"] == "CONNECTION_TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_close_session_transitions_active_session_to_closed() -> None:
    service, _policy, _token_store, audit = build_service()
    session = await service.create_session(
        subject_id="user-1",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token="token-1",
        client_ip="203.0.113.10",
    )

    closed = await service.close_session(
        session_id=session.id,
        subject_id="user-1",
        reason="user_requested",
    )

    assert closed.status is SessionStatus.CLOSED
    assert [event["type"] for event in audit.events][-2:] == [
        "session.closing",
        "session.closed",
    ]


def test_session_api_create_and_close_routes() -> None:
    service, _policy, _token_store, _audit = build_service()

    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "default",
        "permissions": ["sessions:connect"],
    }
    app.dependency_overrides[get_session_gateway_service] = lambda: service
    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/sessions/",
                json={
                    "asset_id": "asset-1",
                    "account_id": "account-1",
                    "protocol": "ssh",
                    "connection_token": "token-1",
                    "client_ip": "203.0.113.10",
                },
            )
            assert create_response.status_code == 201
            created = create_response.json()
            assert created["status"] == "active"

            close_response = client.post(
                f"/api/v1/sessions/{created['id']}/close",
                json={"reason": "user_requested"},
            )
            assert close_response.status_code == 200
            assert close_response.json()["status"] == "closed"
    finally:
        app.dependency_overrides.clear()


def test_session_api_uses_request_client_ip_not_spoofed_body_ip() -> None:
    service, policy, _token_store, audit = build_service()

    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "default",
        "permissions": ["sessions:connect"],
    }
    app.dependency_overrides[get_session_gateway_service] = lambda: service
    try:
        with TestClient(app, client=("198.51.100.24", 50000)) as client:
            response = client.post(
                "/api/v1/sessions/",
                json={
                    "asset_id": "asset-1",
                    "account_id": "account-1",
                    "protocol": "ssh",
                    "connection_token": "token-1",
                    "client_ip": "10.0.0.1",
                },
                headers={"X-Forwarded-For": "10.0.0.2"},
            )

        assert response.status_code == 201
        assert policy.requests[0]["context"]["client_ip"] == "198.51.100.24"
        assert policy.requests[0]["context"]["client_ip_source"] == "request.client"
        assert audit.events[0]["client_ip"] == "198.51.100.24"
        assert audit.events[0]["client_ip_source"] == "request.client"
    finally:
        app.dependency_overrides.clear()
