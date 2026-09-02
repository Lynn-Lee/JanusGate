"""#t68 K8sVaultSessionConnectionResolver 测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.k8s_exec import K8sChannelError
from app.connectors.k8s_vault_resolver import (
    CallableK8sSecretUnwrapper,
    K8sVaultSessionConnectionResolver,
)
from app.connectors.routing_resolver import RoutingSessionConnectionResolver
from app.connectors.session_runtime import ConnectorSessionMode
from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.k8s_cluster import K8sClusterModel

_FAKE_CA = """-----BEGIN CERTIFICATE-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA1234567890abcdef
-----END CERTIFICATE-----"""


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


async def _seed_k8s_stack(session_factory: async_sessionmaker[AsyncSession]) -> Asset:
    async with session_factory() as session:
        platform = Platform(name="K8s", category="cloud", protocols='["k8s"]')
        session.add(platform)
        await session.flush()
        asset = Asset(
            name="prod-k8s",
            address="https://k8s.example",
            tenant_id="tenant-a",
            platform_id=platform.id,
            asset_type="cloud",
            port=443,
            is_active=True,
        )
        session.add(asset)
        await session.flush()
        session.add(
            K8sClusterModel(
                tenant_id="tenant-a",
                asset_id=asset.id,
                api_server="https://k8s.example:6443",
                server_ca_pem=_FAKE_CA,
                namespaces_json='["default", "ops"]',
            )
        )
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="cluster-admin",
                protocol="k8s",
                secret_id="sec_k8s",
                status="active",
                k8s_namespaces_json='["default"]',
                k8s_default_pod="debug-pod",
                k8s_service_account="default",
                k8s_use_short_lived_token=False,
            )
        )
        await session.commit()
        await session.refresh(asset)
        return asset


def _dispatch(asset_id: int) -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id="sess-1",
        connector_id="conn-1",
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id=str(asset_id),
        account_id="cluster-admin",
        protocol="k8s",
    )


@pytest.mark.asyncio
async def test_k8s_resolver_returns_exec_spec_with_namespace_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_stack(session_factory)
    secrets = CallableK8sSecretUnwrapper(lambda secret_id: "long-lived-token")
    resolver = K8sVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=secrets,
    )

    spec = await resolver.resolve(_dispatch(asset.id))
    assert spec.mode is ConnectorSessionMode.K8S_EXEC
    assert spec.k8s is not None
    assert spec.k8s.target.namespace == "default"
    assert spec.k8s.target.pod == "debug-pod"
    assert spec.k8s.credential.token == "long-lived-token"
    assert spec.k8s.scope.namespaces == frozenset({"default"})


@pytest.mark.asyncio
async def test_k8s_resolver_can_issue_short_lived_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_stack(session_factory)
    async with session_factory() as session:
        account = await session.get(Account, 1)
        assert account is not None
        account.k8s_use_short_lived_token = True
        await session.commit()

    secrets = CallableK8sSecretUnwrapper(lambda secret_id: "bootstrap-token")
    resolver = K8sVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=secrets,
    )

    with patch(
        "app.connectors.k8s_vault_resolver.request_service_account_token",
        new=AsyncMock(return_value=type("R", (), {"token": "short-token"})()),
    ):
        spec = await resolver.resolve(_dispatch(asset.id))

    assert spec.k8s is not None
    assert spec.k8s.credential.token == "short-token"


@pytest.mark.asyncio
async def test_routing_resolver_delegates_k8s_protocol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_stack(session_factory)

    class DenySsh:
        async def resolve(self, request: ConnectorDispatchRequest):
            raise PermissionError("SSH_SHOULD_NOT_RUN")

    class DenyDb:
        async def resolve(self, request: ConnectorDispatchRequest):
            raise RuntimeError("DB_SHOULD_NOT_RUN")

    secrets = CallableK8sSecretUnwrapper(lambda secret_id: "long-lived-token")
    k8s_resolver = K8sVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=secrets,
    )
    routing = RoutingSessionConnectionResolver(
        ssh_resolver=DenySsh(), k8s_resolver=k8s_resolver, db_resolver=DenyDb()
    )

    spec = await routing.resolve(_dispatch(asset.id))
    assert spec.mode is ConnectorSessionMode.K8S_EXEC


@pytest.mark.asyncio
async def test_k8s_resolver_requires_default_pod(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_stack(session_factory)
    async with session_factory() as session:
        account = await session.get(Account, 1)
        assert account is not None
        account.k8s_default_pod = ""
        await session.commit()

    secrets = CallableK8sSecretUnwrapper(lambda secret_id: "token")
    resolver = K8sVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=secrets,
    )

    with pytest.raises(K8sChannelError, match="K8S_POD_NOT_CONFIGURED"):
        await resolver.resolve(_dispatch(asset.id))
