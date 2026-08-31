"""#t64：会话创建必须走 AssetPermission；无授权当不存在。"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.sessions.routes import get_session_gateway_service
from app.api.sessions.service import (
    ConnectionToken,
    PolicyDecisionServiceClient,
    SessionGatewayService,
    SessionStatus,
)
from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Asset
from app.models.asset_tree import ASSET_RESOURCE, CONNECT_ACTION, AssetPermissionModel
from app.policy.decision import PolicyDecisionService
from app.policy.repository import build_tenant_policy_service
from app.tenancy.scope import ActorScope


class FakeTokenStore:
    def __init__(self, token: ConnectionToken) -> None:
        self.token = token
        self.consumed = False

    async def consume(self, token_id: str, now: datetime) -> ConnectionToken:
        assert token_id == self.token.token_id
        self.consumed = True
        return self.token


class FakeConnectorScheduler:
    async def dispatch(self, request) -> dict:  # noqa: ANN001
        return {
            "connector_session_id": f"connector-{request.session_id}",
            "connection_url": f"wss://connector.example/sessions/{request.session_id}",
        }

    async def release(self, connector_session_id: str) -> None:
        return None


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


class RaisingPolicy:
    def evaluate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("decision exploded")


def _token() -> ConnectionToken:
    now = datetime.now(UTC)
    return ConnectionToken(
        token_id="token-1",
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        account_id="root",
        protocol="ssh",
        action="session.connect",
        jit_grant_id="",
        workflow_request_id="",
        connector_id="connector-1",
        expires_at=now + timedelta(minutes=5),
    )


def _perm(*, subject_id: str = "user-1", subject_type: str = "user") -> AssetPermissionModel:
    return AssetPermissionModel(
        id="ap-10",
        tenant_id="tenant-a",
        subject_id=subject_id,
        subject_type=subject_type,
        resource_type=ASSET_RESOURCE,
        resource_id="10",
        account_id="",
        protocol="",
        action=CONNECT_ACTION,
        expires_at=None,
    )


def _gateway(policy: PolicyDecisionService) -> tuple[SessionGatewayService, FakeTokenStore]:
    token = _token()
    store = FakeTokenStore(token)
    return (
        SessionGatewayService(
            policy_client=PolicyDecisionServiceClient(policy),
            token_store=store,
            connector_scheduler=FakeConnectorScheduler(),
            audit_sink=FakeAuditSink(),
        ),
        store,
    )


@pytest.mark.asyncio
async def test_create_session_denied_without_asset_permission() -> None:
    service, store = _gateway(PolicyDecisionService(asset_permissions=[]))
    with pytest.raises(PermissionError, match="ASSET_PERMISSION_DENIED"):
        await service.create_session(
            subject_id="user-1",
            tenant_id="tenant-a",
            asset_id="10",
            account_id="root",
            protocol="ssh",
            connection_token="token-1",
        )
    assert store.consumed is False


@pytest.mark.asyncio
async def test_create_session_allows_with_asset_permission() -> None:
    service, store = _gateway(
        PolicyDecisionService(asset_permissions=[_perm()], asset_node_ids={"10": None})
    )
    session = await service.create_session(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        account_id="root",
        protocol="ssh",
        connection_token="token-1",
    )
    assert session.status is SessionStatus.ACTIVE
    assert store.consumed is True


@pytest.mark.asyncio
async def test_create_session_allows_with_group_asset_permission() -> None:
    service, store = _gateway(
        PolicyDecisionService(
            asset_permissions=[_perm(subject_id="ops", subject_type="user_group")],
            asset_node_ids={"10": None},
        )
    )
    session = await service.create_session(
        subject_id="user-1",
        subject_group_ids=("ops",),
        tenant_id="tenant-a",
        asset_id="10",
        account_id="root",
        protocol="ssh",
        connection_token="token-1",
    )
    assert session.status is SessionStatus.ACTIVE
    assert store.consumed is True


@pytest.mark.asyncio
async def test_admin_does_not_bypass_missing_asset_permission() -> None:
    service, store = _gateway(PolicyDecisionService(asset_permissions=[]))
    with pytest.raises(PermissionError, match="ASSET_PERMISSION_DENIED"):
        await service.create_session(
            subject_id="admin-1",
            tenant_id="tenant-a",
            asset_id="10",
            account_id="root",
            protocol="ssh",
            connection_token="token-1",
        )
    assert store.consumed is False


@pytest.mark.asyncio
async def test_evaluate_failure_is_fail_closed() -> None:
    service, store = _gateway(RaisingPolicy())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="POLICY_EVALUATE_FAILED"):
        await service.create_session(
            subject_id="user-1",
            tenant_id="tenant-a",
            asset_id="10",
            account_id="root",
            protocol="ssh",
            connection_token="token-1",
        )
    assert store.consumed is False


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


def install_user(*, permissions: list[str], user_id: str = "user-1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": "tenant-a",
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


async def seed_asset(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(
            Asset(
                id=10,
                name="web-1",
                address="10.0.0.10",
                tenant_id="tenant-a",
                platform_id=1,
            )
        )
        await session.commit()


async def seed_permission(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(_perm())
        await session.commit()


@pytest.mark.asyncio
async def test_http_create_session_without_permission_is_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed_asset(session_factory)
    install_user(permissions=["admin"])
    payload = {
        "asset_id": "10",
        "account_id": "root",
        "protocol": "ssh",
        "connection_token": "token-1",
    }
    try:
        with TestClient(app) as client:
            denied = client.post("/api/v1/sessions/", json=payload)
            assert denied.status_code == 404
            body = denied.json()
            assert body["message"] == "资产不存在"
            assert "没有权限" not in str(body)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_loaded_asset_permission_can_create_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_permission(session_factory)
    async with session_factory() as session:
        policy = await build_tenant_policy_service(
            session,
            ActorScope(user_id="user-1", tenant_id="tenant-a", permissions=("admin",)),
        )
    service, store = _gateway(policy)
    record = await service.create_session(
        subject_id="user-1",
        tenant_id="tenant-a",
        asset_id="10",
        account_id="root",
        protocol="ssh",
        connection_token="token-1",
    )
    assert record.status is SessionStatus.ACTIVE
    assert store.consumed is True


@pytest.mark.asyncio
async def test_build_tenant_policy_service_allows_seeded_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_permission(session_factory)
    async with session_factory() as session:
        policy = await build_tenant_policy_service(
            session,
            ActorScope(user_id="user-1", tenant_id="tenant-a", permissions=("admin",)),
        )
    client = PolicyDecisionServiceClient(policy)
    allowed = await client.evaluate(
        {
            "subject": {"id": "user-1", "type": "user", "tenant_id": "tenant-a"},
            "action": "session.connect",
            "resource": {"id": "10", "type": "asset", "tenant_id": "tenant-a"},
            "context": {"account_id": "root", "protocol": "ssh"},
            "connector_trusted": True,
        }
    )
    denied = await client.evaluate(
        {
            "subject": {"id": "user-1", "type": "user", "tenant_id": "tenant-a"},
            "action": "session.connect",
            "resource": {"id": "99", "type": "asset", "tenant_id": "tenant-a"},
            "context": {"account_id": "root", "protocol": "ssh"},
            "connector_trusted": True,
        }
    )
    assert allowed["decision"] == "allow"
    assert allowed["reason_code"] == "ASSET_PERMISSION_ALLOWED"
    assert denied["decision"] == "deny"
    assert denied["reason_code"] == "ASSET_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_default_session_gateway_dependency_binds_asset_permission_client() -> None:
    service = await get_session_gateway_service(
        object(),  # type: ignore[arg-type]
        {"id": "user-1", "tenant_id": "tenant-a", "permissions": []},
    )
    assert isinstance(service.policy_client, PolicyDecisionServiceClient)
    decision = await service.policy_client.evaluate(
        {
            "subject": {"id": "user-1", "type": "user", "tenant_id": "tenant-a"},
            "action": "session.connect",
            "resource": {"id": "10", "type": "asset", "tenant_id": "tenant-a"},
            "context": {},
            "connector_trusted": True,
        }
    )
    assert decision["decision"] == "deny"
