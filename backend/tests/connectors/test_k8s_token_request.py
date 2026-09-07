"""#t68 K8s TokenRequest：默认关=#t72；开启签发短期令牌；失败 fail-closed 无长期回退。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.asset_vault_resolver import (
    K8S_CONNECT_DENIED_COPY,
    AssetVaultSessionConnectionResolver,
    MappingSecretUnwrapper,
)
from app.connectors.host_key_trust import HostKeyTrustStore
from app.connectors.k8s_exec import (
    TOKEN_TTL_DEFAULT,
    TOKEN_TTL_MAX,
    TOKEN_TTL_MIN,
    K8sChannelError,
    K8sCredential,
    K8sPodInfo,
    clamp_token_ttl_seconds,
    request_service_account_token,
)
from app.connectors.session_runtime import ConnectorSessionMode
from app.connectors.ssh_hostkey import HostKeyScan
from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset, Platform

CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA\n-----END CERTIFICATE-----\n"
BOOTSTRAP = "vault-bootstrap-never-as-session"
SHORT = "short-lived-session-token"


class FakeScanner:
    async def scan(self, host: str, port: int) -> HostKeyScan:
        return HostKeyScan(
            host=host,
            port=port,
            key_type="ssh-ed25519",
            public_key="ssh-ed25519 unused",
            fingerprint="SHA256:unused",
        )


class FakePodLister:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> list[K8sPodInfo]:
        self.calls.append(kwargs)
        return [K8sPodInfo(name="web-0", containers=("app",))]


class FakeTokenRequester:
    def __init__(
        self,
        *,
        token: str = SHORT,
        error: Exception | None = None,
    ) -> None:
        self.token = token
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.token


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


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    use_token_request: bool = False,
    token_ttl_seconds: int = TOKEN_TTL_DEFAULT,
) -> Asset:
    async with session_factory() as session:
        from sqlalchemy import select

        existing = (
            await session.execute(select(Platform).where(Platform.name == "Kubernetes"))
        ).scalar_one_or_none()
        if existing is None:
            platform = Platform(
                name="Kubernetes",
                category="cloud",
                asset_type="cloud",
                protocols='["k8s"]',
            )
            session.add(platform)
            await session.flush()
        else:
            platform = existing
        asset = Asset(
            name="prod-cluster",
            address="https://k8s.internal:6443",
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=443,
            username="",
            is_active=True,
            namespace="prod",
            server_ca=CA_PEM,
        )
        session.add(asset)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="deploy",
                protocol="k8s",
                secret_id="sec_k8s",
                status="active",
                use_token_request=use_token_request,
                token_ttl_seconds=token_ttl_seconds,
            )
        )
        await session.commit()
        await session.refresh(asset)
        return asset


def _dispatch(asset_id: int) -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id="sess-k8s",
        connector_id="conn-1",
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id=str(asset_id),
        account_id="deploy",
        protocol="k8s",
        pod="web-0",
        container="app",
    )


def _resolver(
    session_factory: async_sessionmaker[AsyncSession],
    token_requester: FakeTokenRequester | None = None,
) -> tuple[AssetVaultSessionConnectionResolver, FakeTokenRequester]:
    requester = token_requester or FakeTokenRequester()
    resolver = AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({"sec_k8s": BOOTSTRAP}),
        host_keys=HostKeyTrustStore(session_factory),
        scanner=FakeScanner(),
        k8s_pod_lister=FakePodLister(),
        k8s_token_requester=requester,
    )
    return resolver, requester


def test_clamp_token_ttl_defaults_and_bounds() -> None:
    assert clamp_token_ttl_seconds(None) == TOKEN_TTL_DEFAULT
    assert clamp_token_ttl_seconds(900) == 900
    assert clamp_token_ttl_seconds(1) == TOKEN_TTL_MIN
    assert clamp_token_ttl_seconds(59) == TOKEN_TTL_MIN
    assert clamp_token_ttl_seconds(3601) == TOKEN_TTL_MAX
    assert clamp_token_ttl_seconds(86400) == TOKEN_TTL_MAX


async def test_default_off_uses_vault_token_like_t72(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed(session_factory, use_token_request=False)
    resolver, requester = _resolver(session_factory)
    spec = await resolver.resolve(_dispatch(asset.id))
    assert spec.mode is ConnectorSessionMode.K8S
    assert spec.k8s_credential is not None
    assert spec.k8s_credential.token == BOOTSTRAP
    assert requester.calls == []


async def test_token_request_on_success_uses_short_token_not_bootstrap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed(session_factory, use_token_request=True, token_ttl_seconds=600)
    resolver, requester = _resolver(session_factory)
    spec = await resolver.resolve(_dispatch(asset.id))
    assert spec.k8s_credential is not None
    assert spec.k8s_credential.token == SHORT
    assert BOOTSTRAP not in (spec.k8s_credential.token,)
    assert len(requester.calls) == 1
    call = requester.calls[0]
    assert call["namespace"] == "prod"
    assert call["service_account"] == "deploy"
    assert call["expiration_seconds"] == 600
    cred = call["bootstrap_credential"]
    assert isinstance(cred, K8sCredential)
    assert cred.token == BOOTSTRAP


async def test_token_request_failure_fail_closed_no_long_token_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed(session_factory, use_token_request=True)
    requester = FakeTokenRequester(
        error=K8sChannelError("K8S_TOKEN_REQUEST_FAILED", "boom")
    )
    resolver, _ = _resolver(session_factory, token_requester=requester)
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.resolve(_dispatch(asset.id))
    assert excinfo.value.code == "K8S_TOKEN_REQUEST_FAILED"
    assert excinfo.value.detail == K8S_CONNECT_DENIED_COPY
    assert "令牌" not in excinfo.value.detail
    assert "过期" not in excinfo.value.detail
    assert BOOTSTRAP not in excinfo.value.detail


async def test_token_request_ttl_clamped_when_account_out_of_range(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed(
        session_factory, use_token_request=True, token_ttl_seconds=99999
    )
    resolver, requester = _resolver(session_factory)
    await resolver.resolve(_dispatch(asset.id))
    assert requester.calls[0]["expiration_seconds"] == TOKEN_TTL_MAX


async def test_request_service_account_token_http_mock_success(monkeypatch) -> None:
    from app.connectors import k8s_exec as mod

    monkeypatch.setattr(mod, "_build_ssl_context", lambda target: True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/serviceaccounts/deploy/token" in str(request.url)
        assert request.headers["Authorization"] == f"Bearer {BOOTSTRAP}"
        return httpx.Response(201, json={"status": {"token": SHORT}})

    transport = httpx.MockTransport(handler)
    token = await request_service_account_token(
        api_server="https://k8s.internal:6443",
        namespace="prod",
        service_account="deploy",
        server_ca=CA_PEM,
        bootstrap_credential=K8sCredential(token=BOOTSTRAP),
        expiration_seconds=900,
        transport=transport,
    )
    assert token == SHORT


async def test_request_service_account_token_http_mock_failure(monkeypatch) -> None:
    from app.connectors import k8s_exec as mod

    monkeypatch.setattr(mod, "_build_ssl_context", lambda target: True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, json={"kind": "Status"})
    )
    with pytest.raises(K8sChannelError) as excinfo:
        await request_service_account_token(
            api_server="https://k8s.internal:6443",
            namespace="prod",
            service_account="deploy",
            server_ca=CA_PEM,
            bootstrap_credential=K8sCredential(token=BOOTSTRAP),
            expiration_seconds=900,
            transport=transport,
        )
    assert excinfo.value.code == "K8S_TOKEN_REQUEST_FAILED"
