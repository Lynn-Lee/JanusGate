"""#t69 生产 SessionConnectionResolver：资产注册表 + Vault + 主机密钥 fail-closed。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.routes import get_session_gateway_service
from app.api.sessions.service import (
    ConnectionToken,
    ConnectorDispatchRequest,
    InMemorySessionStore,
    SessionGatewayService,
)
from app.connectors.asset_vault_resolver import (
    AssetVaultSessionConnectionResolver,
    MappingSecretUnwrapper,
)
from app.connectors.host_key_trust import (
    CONNECT_DENIED_COPY,
    HOST_KEY_CHANGED_TITLE,
    HOST_KEY_UNKNOWN_TITLE,
    HostKeyTrustService,
    HostKeyTrustStore,
    classify_presented_key,
)
from app.connectors.session_runtime import ConnectorSessionMode
from app.connectors.ssh_hostkey import HostKeyScan
from app.core.database import Base
from app.core.deps import current_user
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.host_key import HostKeyPresentation


@dataclass
class FakeScanner:
    public_key: str
    fingerprint: str = "SHA256:fake"
    calls: int = 0

    async def scan(self, host: str, port: int) -> HostKeyScan:
        self.calls += 1
        return HostKeyScan(
            host=host,
            port=port,
            key_type="ssh-ed25519",
            public_key=self.public_key,
            fingerprint=self.fingerprint,
        )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_ssh_asset(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    protocol: str = "ssh",
    secret_id: str = "sec_root",
) -> Asset:
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", protocols='["ssh","sftp"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="prod-ssh",
            address="10.0.0.10",
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=22,
            username="root",
            is_active=True,
        )
        session.add(asset)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="root",
                protocol=protocol,
                secret_id=secret_id,
                status="active",
            )
        )
        await session.commit()
        await session.refresh(asset)
        return asset


def _dispatch(asset_id: int, *, protocol: str = "ssh") -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id="sess-1",
        connector_id="conn-1",
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id=str(asset_id),
        account_id="root",
        protocol=protocol,
    )


def _resolver(
    session_factory: async_sessionmaker[AsyncSession],
    scanner: FakeScanner,
    *,
    secret_id: str = "sec_root",
    plaintext: str = "s3cret",
) -> tuple[AssetVaultSessionConnectionResolver, HostKeyTrustStore]:
    store = HostKeyTrustStore(session_factory)
    resolver = AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({secret_id: plaintext}),
        host_keys=store,
        scanner=scanner,
    )
    return resolver, store


def test_unknown_key_copy_is_not_change_warning() -> None:
    presented = HostKeyScan(
        host="10.0.0.10",
        port=22,
        key_type="ssh-ed25519",
        public_key="ssh-ed25519 AAAAUNKNOWN",
        fingerprint="SHA256:unknown",
    )
    result = classify_presented_key(approved_public_key="", presented=presented)
    assert result.state is HostKeyPresentation.UNKNOWN
    assert result.title == HOST_KEY_UNKNOWN_TITLE
    assert HOST_KEY_CHANGED_TITLE not in result.title


def test_changed_key_copy_is_heavier_and_not_unknown_sentence() -> None:
    presented = HostKeyScan(
        host="10.0.0.10",
        port=22,
        key_type="ssh-ed25519",
        public_key="ssh-ed25519 AAAANEW",
        fingerprint="SHA256:new",
    )
    result = classify_presented_key(
        approved_public_key="ssh-ed25519 AAAAOLD", presented=presented
    )
    assert result.state is HostKeyPresentation.CHANGED
    assert result.title == HOST_KEY_CHANGED_TITLE
    assert result.title != HOST_KEY_UNKNOWN_TITLE
    assert "确认这台主机" not in result.title


async def test_no_approved_host_key_cannot_connect_and_does_not_tofu(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_ssh_asset(session_factory)
    scanner = FakeScanner(public_key="ssh-ed25519 AAAAUNKNOWN")
    resolver, store = _resolver(session_factory, scanner)

    with pytest.raises(PermissionError, match="HOST_KEY_UNAPPROVED"):
        await resolver.resolve(_dispatch(asset.id))

    row = await store.get(tenant_id="tenant-a", asset_id=str(asset.id))
    assert row is None or row.approved_public_key == ""
    assert scanner.calls == 1


async def test_rejected_or_pending_never_uses_permission_denied_copy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_ssh_asset(session_factory)
    scanner = FakeScanner(public_key="ssh-ed25519 AAAAPEND")
    resolver, store = _resolver(session_factory, scanner)
    trust = HostKeyTrustService(session_factory=session_factory, store=store, scanner=scanner)
    await trust.overlay_request_metadata(
        tenant_id="tenant-a",
        asset_id=str(asset.id),
        protocol="ssh",
        metadata={},
    )
    await trust.apply_workflow_decision(
        tenant_id="tenant-a",
        asset_id=str(asset.id),
        approved=False,
        metadata={
            "host_key": {
                "public_key": "ssh-ed25519 AAAAPEND",
                "fingerprint": "SHA256:fake",
            }
        },
    )
    with pytest.raises(PermissionError, match="HOST_KEY_UNAPPROVED") as excinfo:
        await resolver.resolve(_dispatch(asset.id))
    assert "没有权限" not in str(excinfo.value)
    assert CONNECT_DENIED_COPY == "无法连接"


@pytest.mark.parametrize("protocol,mode", [
    ("exec", ConnectorSessionMode.EXEC),
    ("ssh", ConnectorSessionMode.INTERACTIVE),
    ("interactive", ConnectorSessionMode.INTERACTIVE),
    ("sftp", ConnectorSessionMode.SFTP),
])
async def test_ssh_modes_resolve_via_registry_and_vault_when_host_key_approved(
    session_factory: async_sessionmaker[AsyncSession],
    protocol: str,
    mode: ConnectorSessionMode,
) -> None:
    asset = await _seed_ssh_asset(session_factory, protocol=protocol)
    key = "ssh-ed25519 AAAAAPPROVED"
    scanner = FakeScanner(public_key=key)
    resolver, store = _resolver(session_factory, scanner)
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(asset.id),
        public_key=key,
        fingerprint="SHA256:fake",
        host=asset.address,
        port=asset.port,
    )

    spec = await resolver.resolve(_dispatch(asset.id, protocol=protocol))
    assert spec.mode is mode
    assert spec.target.host == "10.0.0.10"
    assert spec.target.trusted_host_key == key
    assert spec.credential.password == "s3cret"


async def test_changed_key_with_stale_approval_cannot_connect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_ssh_asset(session_factory)
    store = HostKeyTrustStore(session_factory)
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(asset.id),
        public_key="ssh-ed25519 AAAAOLD",
        fingerprint="SHA256:old",
    )
    scanner = FakeScanner(public_key="ssh-ed25519 AAAANEW")
    resolver, _ = _resolver(session_factory, scanner)
    resolver._host_keys = store
    with pytest.raises(PermissionError, match="HOST_KEY_UNAPPROVED"):
        await resolver.resolve(_dispatch(asset.id))


async def test_k8s_protocol_is_not_resolved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_ssh_asset(session_factory)
    scanner = FakeScanner(public_key="ssh-ed25519 AAAAAPPROVED")
    resolver, store = _resolver(session_factory, scanner)
    await store.approve_presented(
        tenant_id="tenant-a",
        asset_id=str(asset.id),
        public_key="ssh-ed25519 AAAAAPPROVED",
        fingerprint="SHA256:fake",
    )
    with pytest.raises(PermissionError, match="HOST_KEY_UNAPPROVED"):
        await resolver.resolve(_dispatch(asset.id, protocol="k8s"))


def test_session_api_maps_unapproved_host_key_to_cannot_connect() -> None:
    class DenyHostKeyScheduler:
        async def dispatch(self, request) -> dict:  # noqa: ANN001
            raise PermissionError("HOST_KEY_UNAPPROVED")

        async def release(self, connector_session_id: str) -> None:
            return None

    class AllowPolicy:
        async def evaluate(self, request: dict) -> dict:
            return {
                "decision": "allow",
                "reason_code": "EXPLICIT_ALLOW",
                "explain": [],
                "ttl_seconds": 300,
                "obligations": [],
            }

    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    token = ConnectionToken(
        token_id="token-1",
        subject_id="1",
        tenant_id="tenant-a",
        asset_id="1",
        account_id="root",
        protocol="ssh",
        connector_id="connector-1",
        expires_at=now + timedelta(minutes=5),
    )
    class FakeTokenStore:
        async def consume(self, token_id: str, now: object) -> ConnectionToken:
            assert token_id == "token-1"
            return token

    service = SessionGatewayService(
        policy_client=AllowPolicy(),
        token_store=FakeTokenStore(),
        connector_scheduler=DenyHostKeyScheduler(),
        session_store=InMemorySessionStore(),
        now=lambda: now,
    )
    app.dependency_overrides[current_user] = lambda: {
        "id": "1",
        "username": "alice",
        "tenant_id": "tenant-a",
        "permissions": ["sessions:connect"],
    }
    app.dependency_overrides[get_session_gateway_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/sessions/",
                json={
                    "asset_id": "1",
                    "account_id": "root",
                    "protocol": "ssh",
                    "connection_token": "token-1",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    body = response.json()
    assert body["message"] == "无法连接"
    assert "没有权限" not in str(body)
