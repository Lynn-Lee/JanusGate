"""Session Gateway lifecycle and API tests."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.routes import (
    build_connection_token_store,
    get_read_session_gateway_service,
    get_session_gateway_service,
)
from app.api.sessions.service import (
    ConnectionToken,
    InMemoryConnectionTokenStore,
    InMemorySessionStore,
    JitGrantSessionBinding,
    PolicyDecisionServiceClient,
    RedisConnectionTokenStore,
    SessionGatewayService,
    SessionRecord,
    SessionStatus,
    SqlAlchemySessionStore,
)
from app.api.workflows.service import (
    InMemoryWorkflowStore,
    JitGrantStatus,
    WorkflowRequestStatus,
    WorkflowService,
)
from app.core.database import Base
from app.core.deps import current_user
from app.main import app
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import PolicyRule


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


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int, bool]] = []
        self.getdel_calls: list[str] = []

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        self.getdel_calls.append(key)
        return self.values.pop(key, None)


class MultiTokenStore:
    def __init__(self, tokens: dict[str, ConnectionToken]) -> None:
        self.tokens = tokens
        self.consumed: list[str] = []

    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        token = self.tokens.pop(token_id)
        self.consumed.append(token_id)
        return token


class FakeConnectorScheduler:
    def __init__(self) -> None:
        self.dispatched: list[str] = []
        self.released: list[str] = []

    async def dispatch(self, request) -> dict:  # noqa: ANN001 - test double
        self.dispatched.append(request.session_id)
        return {
            "connector_session_id": f"connector-{request.session_id}",
            "connection_url": f"wss://connector.example/sessions/{request.session_id}",
        }

    async def release(self, connector_session_id: str) -> None:
        self.released.append(connector_session_id)


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


class FakeJitGrantClient:
    def __init__(self) -> None:
        self.validated: list[dict] = []
        self.bound_sessions: list[tuple[str, str]] = []

    async def validate_for_session(
        self,
        *,
        jit_grant_id: str,
        subject_id: str,
        tenant_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantSessionBinding:
        self.validated.append(
            {
                "jit_grant_id": jit_grant_id,
                "subject_id": subject_id,
                "tenant_id": tenant_id,
                "asset_id": asset_id,
                "account_id": account_id,
                "protocol": protocol,
                "action": action,
                "now": now,
            }
        )
        return JitGrantSessionBinding(
            jit_grant_id=jit_grant_id,
            workflow_request_id="wr-1",
            expires_at=now + timedelta(minutes=30),
            constraints={"usage": "single-use"},
        )

    async def mark_session_bound(self, *, jit_grant_id: str, session_id: str) -> None:
        self.bound_sessions.append((jit_grant_id, session_id))


class ConcurrentSingleUseJitGrantClient:
    def __init__(self, *, now: datetime) -> None:
        self.now = now
        self.validated = 0
        self.used = False
        self.bound_sessions: list[tuple[str, str]] = []
        self._both_validated = asyncio.Event()

    async def validate_for_session(
        self,
        *,
        jit_grant_id: str,
        subject_id: str,
        tenant_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantSessionBinding:
        if self.used:
            raise PermissionError("JIT_GRANT_NOT_ACTIVE:used")
        self.validated += 1
        if self.validated == 2:
            self._both_validated.set()
        await asyncio.wait_for(self._both_validated.wait(), timeout=1)
        return JitGrantSessionBinding(
            jit_grant_id=jit_grant_id,
            workflow_request_id="wr-1",
            expires_at=self.now + timedelta(minutes=30),
            constraints={
                "subject_id": subject_id,
                "asset_id": asset_id,
                "account_id": account_id,
                "protocol": protocol,
                "action": action,
                "usage": "single-use",
                "max_uses": 1,
                "used_count": 0,
            },
        )

    async def mark_session_bound(self, *, jit_grant_id: str, session_id: str) -> None:
        if self.used:
            raise PermissionError("JIT_GRANT_NOT_ACTIVE:used")
        self.used = True
        self.bound_sessions.append((jit_grant_id, session_id))


def build_service(
    *,
    policy_decision: str = "allow",
    expires_at: datetime | None = None,
    jit_grant_client: FakeJitGrantClient | None = None,
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
        jit_grant_client=jit_grant_client,
        now=lambda: now,
    )
    return service, policy, token_store, audit


async def build_approved_workflow_grant(
    *,
    now: datetime,
    workflow_request_id: str = "wr-1",
    jit_grant_id: str = "grant-1",
    subject_id: str = "user-1",
    tenant_id: str = "default",
    asset_id: str = "asset-1",
    account_id: str = "account-1",
    protocol: str = "ssh",
    ttl_seconds: int = 1800,
) -> WorkflowService:
    workflow_service = WorkflowService(
        store=InMemoryWorkflowStore(),
        now=lambda: now,
        request_id_factory=lambda: workflow_request_id,
        grant_id_factory=lambda: jit_grant_id,
    )
    requester = {"id": subject_id, "username": "alice", "tenant_id": tenant_id}
    approver = {
        "id": "approver-1",
        "username": "bob",
        "tenant_id": tenant_id,
        "permissions": ["workflow:approve"],
    }
    await workflow_service.create_request(
        actor=requester,
        asset_id=asset_id,
        account_id=account_id,
        protocol=protocol,
        action="session.connect",
        reason="临时排障",
        requested_ttl_seconds=ttl_seconds,
        metadata={},
    )
    await workflow_service.submit_request(workflow_request_id, actor_id=subject_id, tenant_id=tenant_id)
    await workflow_service.approve_request(
        workflow_request_id,
        actor=approver,
        decision_reason="允许排障",
        grant_ttl_seconds=ttl_seconds,
    )
    return workflow_service


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
        tenant_id="default",
    )

    assert session.status is SessionStatus.ACTIVE
    assert session.asset_id == "asset-1"
    assert session.account_id == "account-1"
    assert session.connector_id == "connector-1"
    assert session.connection_url == f"wss://connector.example/sessions/{session.id}"
    assert "credential" not in session.model_dump()
    assert token_store.consumed is True
    assert policy.requests[0]["action"] == "session.connect"
    assert "connection_token" not in policy.requests[0]["context"]
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
            tenant_id="default",
        )

    assert token_store.consumed is False
    assert audit.events[-1]["type"] == "session.denied"


@pytest.mark.asyncio
async def test_policy_deny_does_not_consume_real_single_use_jit_grant() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    workflow_service = await build_approved_workflow_grant(now=now)
    service = SessionGatewayService(
        policy_client=FakePolicyClient("deny"),
        token_store=InMemoryConnectionTokenStore(token_id_factory=lambda: "raw-connection-token"),
        connector_scheduler=FakeConnectorScheduler(),
        session_store=InMemorySessionStore(),
        jit_grant_client=workflow_service,
        now=lambda: now,
    )
    issued = await service.issue_connection_token(
        subject_id="user-1",
        tenant_id="default",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        jit_grant_id="grant-1",
    )

    with pytest.raises(PermissionError, match="POLICY_DENY"):
        await service.create_session(
            subject_id="user-1",
            tenant_id="default",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            connection_token=issued.connection_token,
            client_ip="203.0.113.10",
            jit_grant_id="grant-1",
        )

    grant = await workflow_service.get_grant("grant-1", tenant_id="default")
    assert grant is not None
    assert grant.status is JitGrantStatus.ACTIVE
    assert grant.constraints["used_count"] == 0


@pytest.mark.asyncio
async def test_redis_connection_token_store_persists_digest_key_and_consumes_once() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    redis = FakeRedisClient()
    store = RedisConnectionTokenStore(
        redis,
        token_id_factory=lambda: "raw-redis-connection-token",
        key_prefix="test:connection-token:",
    )
    token = ConnectionToken(
        token_id="",
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        jit_grant_id="grant-1",
        workflow_request_id="wr-1",
        connector_id="connector-1",
        expires_at=now + timedelta(minutes=5),
    )

    issue = await store.issue(token, now=now)
    consumed = await store.consume(issue.connection_token, now)

    assert issue.connection_token == "raw-redis-connection-token"
    assert redis.set_calls[0][0].startswith("test:connection-token:")
    assert "raw-redis-connection-token" not in redis.set_calls[0][0]
    assert "raw-redis-connection-token" not in redis.set_calls[0][1]
    assert redis.set_calls[0][2:] == (300, True)
    assert redis.getdel_calls == [redis.set_calls[0][0]]
    assert consumed.token_id == redis.set_calls[0][0].removeprefix("test:connection-token:")
    assert consumed.subject_id == "user-1"
    assert consumed.tenant_id == "tenant-a"
    assert await redis.getdel(redis.set_calls[0][0]) is None


@pytest.mark.asyncio
async def test_redis_connection_token_store_rejects_missing_or_replayed_token() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    store = RedisConnectionTokenStore(FakeRedisClient())

    with pytest.raises(ValueError, match="CONNECTION_TOKEN_NOT_FOUND"):
        await store.consume("missing-token", now)


def test_connection_token_store_factory_can_select_redis_backend() -> None:
    redis = FakeRedisClient()

    store = build_connection_token_store(
        store="redis",
        redis_url="redis://redis.example/0",
        redis_key_prefix="test:connection-token:",
        redis_factory=lambda url: redis,
    )

    assert isinstance(store, RedisConnectionTokenStore)


@pytest.mark.asyncio
async def test_concurrent_single_use_grant_allows_only_one_connector_dispatch() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    jit_grant_client = ConcurrentSingleUseJitGrantClient(now=now)
    scheduler = FakeConnectorScheduler()
    token_store = MultiTokenStore(
        {
            "raw-token-1": ConnectionToken(
                token_id="digest-1",
                subject_id="user-1",
                tenant_id="default",
                asset_id="asset-1",
                account_id="account-1",
                connector_id="connector-1",
                protocol="ssh",
                jit_grant_id="grant-1",
                expires_at=now + timedelta(minutes=5),
            ),
            "raw-token-2": ConnectionToken(
                token_id="digest-2",
                subject_id="user-1",
                tenant_id="default",
                asset_id="asset-1",
                account_id="account-1",
                connector_id="connector-1",
                protocol="ssh",
                jit_grant_id="grant-1",
                expires_at=now + timedelta(minutes=5),
            ),
        }
    )
    session_ids = iter(["session-1", "session-2"])
    service = SessionGatewayService(
        policy_client=FakePolicyClient("allow"),
        token_store=token_store,
        connector_scheduler=scheduler,
        session_store=InMemorySessionStore(),
        jit_grant_client=jit_grant_client,
        now=lambda: now,
        session_id_factory=lambda: next(session_ids),
    )

    async def create(connection_token: str) -> object:
        return await service.create_session(
            subject_id="user-1",
            tenant_id="default",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            connection_token=connection_token,
            client_ip="203.0.113.10",
            jit_grant_id="grant-1",
        )

    results = await asyncio.gather(
        create("raw-token-1"),
        create("raw-token-2"),
        return_exceptions=True,
    )

    successes = [result for result in results if isinstance(result, SessionRecord)]
    failures = [result for result in results if isinstance(result, PermissionError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert str(failures[0]) == "JIT_GRANT_NOT_ACTIVE:used"
    assert len(scheduler.dispatched) == 1


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
            tenant_id="default",
        )

    assert audit.events[-1]["type"] == "session.failed"
    assert audit.events[-1]["reason_code"] == "CONNECTION_TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_issue_connection_token_for_valid_jit_grant_and_consume_once() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    workflow_service = await build_approved_workflow_grant(now=now)
    audit = FakeAuditSink()
    token_store = InMemoryConnectionTokenStore(token_id_factory=lambda: "raw-connection-token")
    service = SessionGatewayService(
        policy_client=FakePolicyClient(),
        token_store=token_store,
        connector_scheduler=FakeConnectorScheduler(),
        session_store=InMemorySessionStore(),
        audit_sink=audit,
        jit_grant_client=workflow_service,
        now=lambda: now,
        session_id_factory=lambda: "sess-1",
    )

    issued = await service.issue_connection_token(
        subject_id="user-1",
        tenant_id="default",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        jit_grant_id="grant-1",
    )

    assert issued.connection_token == "raw-connection-token"
    assert issued.jit_grant_id == "grant-1"
    assert issued.workflow_request_id == "wr-1"
    assert issued.asset_id == "asset-1"
    assert issued.account_id == "account-1"
    assert issued.protocol == "ssh"
    assert issued.action == "session.connect"
    assert issued.expires_at == now + timedelta(minutes=5)
    assert "raw-connection-token" not in str(audit.events)
    assert audit.events[-1]["type"] == "session.connection_token.issued"

    session = await service.create_session(
        subject_id="user-1",
        tenant_id="default",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token=issued.connection_token,
        client_ip="203.0.113.10",
        jit_grant_id="grant-1",
    )

    assert session.status is SessionStatus.ACTIVE
    assert session.connection_token_id != issued.connection_token
    with pytest.raises(ValueError, match="CONNECTION_TOKEN_NOT_FOUND"):
        await token_store.consume(issued.connection_token, now)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"subject_id": "user-2"}, "JIT_GRANT_SUBJECT_MISMATCH"),
        ({"asset_id": "asset-2"}, "JIT_GRANT_ASSET_MISMATCH"),
        ({"account_id": "account-2"}, "JIT_GRANT_ACCOUNT_MISMATCH"),
        ({"protocol": "rdp"}, "JIT_GRANT_PROTOCOL_MISMATCH"),
    ],
)
async def test_issue_connection_token_rejects_overreach_and_binding_mismatch(
    kwargs: dict[str, str],
    error: str,
) -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    workflow_service = await build_approved_workflow_grant(now=now)
    service = SessionGatewayService(
        policy_client=FakePolicyClient(),
        token_store=InMemoryConnectionTokenStore(token_id_factory=lambda: "raw-connection-token"),
        jit_grant_client=workflow_service,
        now=lambda: now,
    )
    request = {
        "subject_id": "user-1",
        "tenant_id": "default",
        "asset_id": "asset-1",
        "account_id": "account-1",
        "protocol": "ssh",
        "jit_grant_id": "grant-1",
    }
    request.update(kwargs)

    with pytest.raises(PermissionError, match=error):
        await service.issue_connection_token(**request)


@pytest.mark.asyncio
async def test_issue_connection_token_rejects_revoked_or_expired_grant() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    workflow_service = await build_approved_workflow_grant(now=now)
    approver = {
        "id": "approver-1",
        "username": "bob",
        "tenant_id": "default",
        "permissions": ["workflow:approve"],
    }
    await workflow_service.revoke_request("wr-1", actor=approver, reason="risk_changed")
    service = SessionGatewayService(
        policy_client=FakePolicyClient(),
        token_store=InMemoryConnectionTokenStore(token_id_factory=lambda: "raw-connection-token"),
        jit_grant_client=workflow_service,
        now=lambda: now,
    )

    with pytest.raises(PermissionError, match="JIT_GRANT_NOT_ACTIVE:revoked"):
        await service.issue_connection_token(
            subject_id="user-1",
            tenant_id="default",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            jit_grant_id="grant-1",
        )

    expired_workflow = await build_approved_workflow_grant(
        now=now - timedelta(hours=2),
        workflow_request_id="wr-expired",
        jit_grant_id="grant-expired",
        ttl_seconds=60,
    )
    expired_service = SessionGatewayService(
        policy_client=FakePolicyClient(),
        token_store=InMemoryConnectionTokenStore(token_id_factory=lambda: "raw-expired-token"),
        jit_grant_client=expired_workflow,
        now=lambda: now,
    )
    with pytest.raises(PermissionError, match="JIT_GRANT_EXPIRED"):
        await expired_service.issue_connection_token(
            subject_id="user-1",
            tenant_id="default",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            jit_grant_id="grant-expired",
        )


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
        tenant_id="default",
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


@pytest.mark.asyncio
async def test_create_session_binds_valid_jit_grant_and_marks_it_used() -> None:
    jit_grant_client = FakeJitGrantClient()
    service, policy, _token_store, audit = build_service(jit_grant_client=jit_grant_client)

    session = await service.create_session(
        subject_id="user-1",
        tenant_id="tenant-1",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token="token-1",
        client_ip="203.0.113.10",
        jit_grant_id="grant-1",
    )

    assert session.jit_grant_id == "grant-1"
    assert session.workflow_request_id == "wr-1"
    assert jit_grant_client.validated[0]["tenant_id"] == "tenant-1"
    assert jit_grant_client.validated[0]["action"] == "session.connect"
    assert jit_grant_client.bound_sessions == [("grant-1", session.id)]
    assert policy.requests[0]["context"]["jit_grant_id"] == "grant-1"
    assert policy.requests[0]["context"]["account_id"] == "account-1"
    assert policy.requests[0]["context"]["protocol"] == "ssh"
    assert policy.requests[0]["approval"] == {
        "status": "approved",
        "grant_id": "grant-1",
        "workflow_request_id": "wr-1",
        "expires_at": jit_grant_client.validated[0]["now"] + timedelta(minutes=30),
        "constraints": {
            "usage": "single-use",
        },
    }
    assert audit.events[-1]["jit_grant_id"] == "grant-1"


@pytest.mark.asyncio
async def test_real_workflow_grant_is_consumed_once_and_revoke_closes_bound_session() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)
    workflow_service = WorkflowService(
        store=InMemoryWorkflowStore(),
        now=lambda: now,
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    requester = {"id": "user-1", "username": "alice", "tenant_id": "default"}
    approver = {
        "id": "approver-1",
        "username": "bob",
        "tenant_id": "default",
        "permissions": ["workflow:approve"],
    }
    await workflow_service.create_request(
        actor=requester,
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        action="session.connect",
        reason="临时排障",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await workflow_service.submit_request("wr-1", actor_id="user-1", tenant_id="default")
    await workflow_service.approve_request(
        "wr-1",
        actor=approver,
        decision_reason="允许排障",
        grant_ttl_seconds=1800,
    )

    session_service, _policy, _token_store, audit = build_service(jit_grant_client=workflow_service)
    workflow_service.session_revoker = session_service
    session = await session_service.create_session(
        subject_id="user-1",
        tenant_id="default",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token="token-1",
        client_ip="203.0.113.10",
        jit_grant_id="grant-1",
    )

    grant = await workflow_service.get_grant("grant-1", tenant_id="default")
    assert grant is not None
    assert grant.status is JitGrantStatus.USED
    assert session.workflow_request_id == "wr-1"

    with pytest.raises(PermissionError, match="JIT_GRANT_NOT_ACTIVE:used"):
        await session_service.create_session(
            subject_id="user-1",
            tenant_id="default",
            asset_id="asset-1",
            account_id="account-1",
            protocol="ssh",
            connection_token="token-1",
            client_ip="203.0.113.10",
            jit_grant_id="grant-1",
        )

    revoked = await workflow_service.revoke_request(
        "wr-1",
        actor=approver,
        reason="risk_changed",
    )
    assert revoked.status is WorkflowRequestStatus.REVOKED
    assert session.status is SessionStatus.CLOSED
    assert audit.events[-1]["type"] == "session.revoked_by_jit_grant"
    assert audit.events[-1]["jit_grant_id"] == "grant-1"


@pytest.mark.asyncio
async def test_session_gateway_can_authorize_real_workflow_grant_through_policy_service() -> None:
    now = datetime.now(UTC)
    workflow_service = WorkflowService(
        store=InMemoryWorkflowStore(),
        now=lambda: now,
        request_id_factory=lambda: "wr-1",
        grant_id_factory=lambda: "grant-1",
    )
    requester = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1"}
    approver = {
        "id": "approver-1",
        "username": "bob",
        "tenant_id": "tenant-1",
        "permissions": ["workflow:approve"],
    }
    await workflow_service.create_request(
        actor=requester,
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        action="session.connect",
        reason="临时排障",
        requested_ttl_seconds=1800,
        metadata={},
    )
    await workflow_service.submit_request("wr-1", actor_id="user-1", tenant_id="tenant-1")
    await workflow_service.approve_request(
        "wr-1",
        actor=approver,
        decision_reason="允许排障",
        grant_ttl_seconds=1800,
    )

    policy_client = PolicyDecisionServiceClient(
        PolicyDecisionService(
            rules=[
                PolicyRule(
                    id="workflow-jit",
                    subject_ids=["user-1"],
                    actions=["session.connect"],
                    resource_ids=["asset-1"],
                    tenant_id="tenant-1",
                    require_approval=True,
                )
            ]
        )
    )
    session_service, _policy, _token_store, _audit = build_service(jit_grant_client=workflow_service)
    session_service.policy_client = policy_client

    session = await session_service.create_session(
        subject_id="user-1",
        tenant_id="tenant-1",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token="token-1",
        client_ip="203.0.113.10",
        jit_grant_id="grant-1",
    )

    assert session.status is SessionStatus.ACTIVE
    assert session.workflow_request_id == "wr-1"


@pytest.mark.asyncio
async def test_revoke_sessions_by_jit_grant_closes_active_sessions_and_audits() -> None:
    service, _policy, _token_store, audit = build_service(jit_grant_client=FakeJitGrantClient())
    session = await service.create_session(
        subject_id="user-1",
        tenant_id="tenant-1",
        asset_id="asset-1",
        account_id="account-1",
        protocol="ssh",
        connection_token="token-1",
        client_ip="203.0.113.10",
        jit_grant_id="grant-1",
    )
    assert session.status is SessionStatus.ACTIVE

    revoked_session_ids = await service.revoke_sessions_by_jit_grant(
        "grant-1",
        reason="jit_grant_revoked",
    )

    assert revoked_session_ids == [session.id]
    assert session.status is SessionStatus.CLOSED
    assert audit.events[-1]["type"] == "session.revoked_by_jit_grant"
    assert audit.events[-1]["jit_grant_id"] == "grant-1"


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


def test_session_api_lists_only_current_user_sessions() -> None:
    service, _policy, _token_store, _audit = build_service()

    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "default",
        "permissions": ["sessions:connect"],
    }
    app.dependency_overrides[get_session_gateway_service] = lambda: service
    app.dependency_overrides[get_read_session_gateway_service] = lambda: service
    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/sessions/",
                json={
                    "asset_id": "asset-1",
                    "account_id": "account-1",
                    "protocol": "ssh",
                    "connection_token": "token-1",
                },
            )
            assert create_response.status_code == 201

            list_response = client.get("/api/v1/sessions/")

            assert list_response.status_code == 200
            payload = list_response.json()
            assert payload["total"] == 1
            assert payload["items"][0]["id"] == create_response.json()["id"]

        app.dependency_overrides[current_user] = lambda: {
            "id": "user-2",
            "username": "bob",
            "tenant_id": "default",
            "permissions": ["sessions:connect"],
        }
        with TestClient(app) as client:
            other_user_response = client.get("/api/v1/sessions/")

        assert other_user_response.status_code == 200
        assert other_user_response.json() == {"items": [], "total": 0}
    finally:
        app.dependency_overrides.clear()


def test_session_api_issues_real_connection_token_for_frontend() -> None:
    now = datetime(2026, 6, 29, 15, 0, tzinfo=UTC)

    async def make_service() -> SessionGatewayService:
        workflow_service = await build_approved_workflow_grant(now=now)
        return SessionGatewayService(
            policy_client=FakePolicyClient(),
            token_store=InMemoryConnectionTokenStore(token_id_factory=lambda: "api-connection-token"),
            jit_grant_client=workflow_service,
            now=lambda: now,
        )

    service_holder: dict[str, SessionGatewayService] = {}

    async def get_service() -> SessionGatewayService:
        if "service" not in service_holder:
            service_holder["service"] = await make_service()
        return service_holder["service"]

    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": "default",
        "permissions": ["sessions:connect"],
    }
    app.dependency_overrides[get_session_gateway_service] = get_service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/sessions/connection-token",
                json={
                    "jit_grant_id": "grant-1",
                    "asset_id": "asset-1",
                    "account_id": "account-1",
                    "protocol": "ssh",
                },
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["connection_token"] == "api-connection-token"
        assert payload["jit_grant_id"] == "grant-1"
        assert payload["workflow_request_id"] == "wr-1"
        assert payload["action"] == "session.connect"
        assert "credential" not in str(payload).lower()
    finally:
        app.dependency_overrides.clear()


def test_session_connection_token_contract_is_in_openapi() -> None:
    schema = app.openapi()

    assert "/api/v1/sessions/connection-token" in schema["paths"]
    operation = schema["paths"]["/api/v1/sessions/connection-token"]["post"]
    assert operation["responses"]["201"]["description"]
    assert "SessionConnectionTokenRequest" in str(operation["requestBody"])
    assert "SessionConnectionTokenResponse" in str(operation["responses"]["201"])


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


@pytest.mark.asyncio
async def test_default_session_gateway_dependency_binds_real_policy_client() -> None:
    service = await get_session_gateway_service(
        object(),  # type: ignore[arg-type]
        {"id": "user-1", "tenant_id": "default", "permissions": []},
    )

    assert isinstance(service.policy_client, PolicyDecisionServiceClient)


@pytest.mark.asyncio
async def test_default_jit_session_policy_allows_valid_approved_grant_context() -> None:
    policy_client = PolicyDecisionServiceClient(
        PolicyDecisionService(
            rules=[
                PolicyRule(
                    id="approved-jit-session",
                    subject_ids=["*"],
                    actions=["session.connect"],
                    resource_ids=["*"],
                    tenant_id="*",
                    require_approval=True,
                )
            ]
        )
    )

    decision = await policy_client.evaluate(
        {
            "subject": {"id": "user-1", "type": "user", "tenant_id": "default"},
            "action": "session.connect",
            "resource": {"id": "asset-1", "type": "asset", "tenant_id": "default"},
            "context": {
                "account_id": "account-1",
                "protocol": "ssh",
            },
            "approval": {
                "status": "approved",
                "grant_id": "grant-1",
                "workflow_request_id": "wr-1",
                    "expires_at": datetime.now(UTC) + timedelta(minutes=30),
                "constraints": {
                    "subject_id": "user-1",
                    "asset_id": "asset-1",
                    "account_id": "account-1",
                    "protocol": "ssh",
                    "action": "session.connect",
                    "usage": "single-use",
                    "max_uses": 1,
                    "used_count": 0,
                },
            },
            "connector_trusted": True,
        }
    )

    assert decision["decision"] == "allow"
    assert decision["reason_code"] == "POLICY_ALLOWED"


async def test_sqlalchemy_session_store_persists_upserts_and_scopes_listing() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlAlchemySessionStore(session_factory=factory, read_session_factory=factory)

    now = datetime.now(UTC)
    record = SessionRecord(
        id="sess-1",
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        connection_token_id="tok-digest",
        jit_grant_id="grant-1",
        status=SessionStatus.ACTIVE,
        connector_session_id="cs-1",
        created_at=now,
        updated_at=now,
        audit_event_ids=["e1", "e2"],
    )
    await store.save(record)

    fetched = await store.get("sess-1")
    assert fetched is not None
    assert fetched.status is SessionStatus.ACTIVE
    assert fetched.jit_grant_id == "grant-1"
    assert fetched.audit_event_ids == ["e1", "e2"]
    assert fetched.created_at.tzinfo is not None

    # 跨租户但同一 grant/subject 的另一会话
    other = record.model_copy(update={"id": "sess-2", "tenant_id": "tenant-b"})
    await store.save(other)

    mine = await store.list_by_subject(subject_id="user-1", tenant_id="tenant-a")
    assert [session.id for session in mine] == ["sess-1"]
    by_grant = await store.list_by_jit_grant("grant-1")
    assert {session.id for session in by_grant} == {"sess-1", "sess-2"}

    # upsert：关闭会话更新同一行而非新增
    record.status = SessionStatus.CLOSED
    record.closed_at = now
    await store.save(record)
    closed = await store.get("sess-1")
    assert closed is not None
    assert closed.status is SessionStatus.CLOSED
    assert closed.closed_at is not None
    assert len(await store.list_by_jit_grant("grant-1")) == 2

    await engine.dispose()
