"""#t72 生产 K8s SessionConnectionResolver：HTTPS+CA、单一 namespace、建连弹层选 Pod。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

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
    K8S_CONNECT_DENIED_COPY,
    K8S_HTTPS_CA_DENIED_COPY,
    AssetVaultSessionConnectionResolver,
    MappingSecretUnwrapper,
)
from app.connectors.host_key_trust import CONNECT_DENIED_COPY, HostKeyTrustStore
from app.connectors.k8s_exec import K8sChannelError, K8sCredential, K8sPodInfo
from app.connectors.session_runtime import ConnectorSessionMode
from app.connectors.ssh_hostkey import HostKeyScan
from app.core.database import Base
from app.core.deps import current_user
from app.main import app
from app.models.account import Account
from app.models.asset import Asset, Platform
from app.models.host_key import HostKeyPresentation

CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA\n-----END CERTIFICATE-----\n"
TOKEN = "k8s-live-token-never-persist"


class FakeScanner:
    def __init__(self) -> None:
        self.calls = 0

    async def scan(self, host: str, port: int) -> HostKeyScan:
        self.calls += 1
        return HostKeyScan(
            host=host,
            port=port,
            key_type="ssh-ed25519",
            public_key="ssh-ed25519 SHOULD-NOT-SCAN",
            fingerprint="SHA256:unused",
        )


class FakePodLister:
    def __init__(
        self,
        pods: list[K8sPodInfo] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.pods = pods or [K8sPodInfo(name="web-0", containers=("app", "sidecar"))]
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> list[K8sPodInfo]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return list(self.pods)


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


async def _seed_k8s_asset(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    address: str = "https://k8s.internal:6443",
    namespace: str = "prod",
    server_ca: str = CA_PEM,
    secret_id: str = "sec_k8s",
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
            address=address,
            tenant_id="tenant-a",
            platform_id=platform.id,
            port=443,
            username="",
            is_active=True,
            namespace=namespace,
            server_ca=server_ca,
        )
        session.add(asset)
        await session.flush()
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=asset.id,
                username="deploy",
                protocol="k8s",
                secret_id=secret_id,
                status="active",
            )
        )
        await session.commit()
        await session.refresh(asset)
        return asset


def _dispatch(asset_id: int, *, pod: str = "web-0", container: str = "app") -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id="sess-k8s",
        connector_id="conn-1",
        tenant_id="tenant-a",
        subject_id="user-1",
        asset_id=str(asset_id),
        account_id="deploy",
        protocol="k8s",
        pod=pod,
        container=container,
    )


def _resolver(
    session_factory: async_sessionmaker[AsyncSession],
    scanner: FakeScanner | None = None,
    pod_lister: FakePodLister | None = None,
) -> tuple[AssetVaultSessionConnectionResolver, FakeScanner]:
    scan = scanner or FakeScanner()
    resolver = AssetVaultSessionConnectionResolver(
        session_factory=session_factory,
        secrets=MappingSecretUnwrapper({"sec_k8s": TOKEN}),
        host_keys=HostKeyTrustStore(session_factory),
        scanner=scan,
        k8s_pod_lister=pod_lister or FakePodLister(),
    )
    return resolver, scan


async def test_k8s_resolves_from_request_pod_not_asset_and_skips_host_key_scan(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory)
    assert "pod" not in Asset.__table__.columns
    assert "container" not in Asset.__table__.columns
    assert "namespace" in Asset.__table__.columns
    assert "pod" not in Account.__table__.columns
    resolver, scanner = _resolver(session_factory)
    spec = await resolver.resolve(_dispatch(asset.id, pod="web-0", container="app"))
    assert spec.mode is ConnectorSessionMode.K8S
    assert spec.k8s_target is not None
    assert spec.k8s_target.api_server == "https://k8s.internal:6443"
    assert spec.k8s_target.namespace == "prod"
    assert spec.k8s_target.pod == "web-0"
    assert spec.k8s_target.container == "app"
    assert spec.k8s_target.server_ca == CA_PEM.strip()
    assert spec.k8s_scope is not None
    assert spec.k8s_scope.namespaces == frozenset({"prod"})
    assert isinstance(spec.k8s_credential, K8sCredential)
    assert spec.k8s_credential.token == TOKEN
    assert TOKEN not in repr(spec)
    assert TOKEN not in repr(spec.k8s_credential)
    assert scanner.calls == 0
    assert spec.target is None


async def test_resolve_does_not_store_pod_on_asset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory)
    original_ns = asset.namespace
    resolver, _ = _resolver(session_factory)
    await resolver.resolve(_dispatch(asset.id, pod="web-0", container="app"))
    async with session_factory() as session:
        reloaded = await session.get(Asset, asset.id)
    assert reloaded is not None
    assert reloaded.namespace == original_ns
    assert "pod" not in Asset.__table__.columns
    assert "container" not in Asset.__table__.columns


async def test_optional_container_defaults_to_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory)
    resolver, _ = _resolver(session_factory)
    spec = await resolver.resolve(_dispatch(asset.id, pod="web-0", container=""))
    assert spec.k8s_target is not None
    assert spec.k8s_target.pod == "web-0"
    assert spec.k8s_target.container is None


@pytest.mark.parametrize(
    "address,server_ca",
    [
        ("http://k8s.internal:6443", CA_PEM),
        ("https://k8s.internal:6443", ""),
        ("k8s.internal", ""),
    ],
)
async def test_missing_ca_or_http_requires_https_and_ca(
    session_factory: async_sessionmaker[AsyncSession],
    address: str,
    server_ca: str,
) -> None:
    asset = await _seed_k8s_asset(session_factory, address=address, server_ca=server_ca)
    resolver, scanner = _resolver(session_factory)
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.resolve(_dispatch(asset.id))
    assert excinfo.value.code == "K8S_HTTPS_CA_REQUIRED"
    assert excinfo.value.detail == K8S_HTTPS_CA_DENIED_COPY
    assert "没有权限" not in str(excinfo.value)
    assert "越权" not in str(excinfo.value)
    assert scanner.calls == 0
    assert K8S_HTTPS_CA_DENIED_COPY == "无法连接（需要 HTTPS 和 CA）"


async def test_missing_namespace_cannot_connect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory, namespace="")
    resolver, _ = _resolver(session_factory)
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.resolve(_dispatch(asset.id))
    assert excinfo.value.code == "K8S_NAMESPACE_MISSING"
    assert excinfo.value.detail == K8S_CONNECT_DENIED_COPY
    assert "越权" not in str(excinfo.value)
    assert "没有权限" not in str(excinfo.value)


async def test_missing_pod_cannot_connect(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory)
    resolver, _ = _resolver(session_factory)
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.resolve(_dispatch(asset.id, pod="", container=""))
    assert excinfo.value.code == "K8S_POD_REQUIRED"
    assert excinfo.value.detail == K8S_CONNECT_DENIED_COPY


async def test_list_pods_overreach_is_cannot_connect_never_permission_copy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory)
    lister = FakePodLister(
        error=K8sChannelError("K8S_NAMESPACE_FORBIDDEN", "namespace is not within the granted scope")
    )
    resolver, _ = _resolver(session_factory, pod_lister=lister)
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.list_k8s_pods(_dispatch(asset.id, pod="", container=""))
    assert excinfo.value.code == "K8S_NAMESPACE_FORBIDDEN"
    assert "越权" not in str(excinfo.value)
    assert "没有权限" not in str(excinfo.value)
    assert CONNECT_DENIED_COPY == "无法连接"
    assert lister.calls
    assert lister.calls[0]["namespace"] == "prod"


async def test_list_pods_stays_in_asset_namespace(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    asset = await _seed_k8s_asset(session_factory, namespace="prod")
    lister = FakePodLister()
    resolver, scanner = _resolver(session_factory, pod_lister=lister)
    namespace, pods = await resolver.list_k8s_pods(_dispatch(asset.id, pod="", container=""))
    assert namespace == "prod"
    assert [pod.name for pod in pods] == ["web-0"]
    assert lister.calls[0]["namespace"] == "prod"
    assert lister.calls[0]["api_server"] == "https://k8s.internal:6443"
    assert scanner.calls == 0


async def test_account_namespace_does_not_override_asset_namespace(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    asset = await _seed_k8s_asset(session_factory, namespace="prod")
    resolver, _ = _resolver(session_factory)
    from app.connectors import asset_vault_resolver as mod

    real_load = mod._load_account

    async def load_with_account_ns(*args: object, **kwargs: object):
        account = await real_load(*args, **kwargs)
        if account is not None:
            account.namespace = "kube-system"
        return account

    monkeypatch.setattr(mod, "_load_account", load_with_account_ns)
    spec = await resolver.resolve(_dispatch(asset.id, pod="web-0", container="app"))
    assert spec.k8s_target is not None
    assert spec.k8s_target.namespace == "prod"
    assert spec.k8s_scope is not None
    assert spec.k8s_scope.namespaces == frozenset({"prod"})


async def test_list_pods_missing_ns_or_https_does_not_call_lister(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    missing_ns = await _seed_k8s_asset(session_factory, namespace="")
    lister = FakePodLister()
    resolver, _ = _resolver(session_factory, pod_lister=lister)
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.list_k8s_pods(_dispatch(missing_ns.id, pod="", container=""))
    assert excinfo.value.code == "K8S_NAMESPACE_MISSING"
    assert excinfo.value.detail == K8S_CONNECT_DENIED_COPY
    assert lister.calls == []

    http_asset = await _seed_k8s_asset(session_factory, address="http://k8s.internal:6443")
    with pytest.raises(K8sChannelError) as excinfo:
        await resolver.list_k8s_pods(_dispatch(http_asset.id, pod="", container=""))
    assert excinfo.value.code == "K8S_HTTPS_CA_REQUIRED"
    assert excinfo.value.detail == K8S_HTTPS_CA_DENIED_COPY
    assert lister.calls == []


def _post_session(*, protocol: str, scheduler, reason: BaseException | None = None, json_body: dict | None = None):
    class AllowPolicy:
        async def evaluate(self, request: dict) -> dict:
            return {
                "decision": "allow",
                "reason_code": "EXPLICIT_ALLOW",
                "explain": [],
                "ttl_seconds": 300,
                "obligations": [],
            }

    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    token = ConnectionToken(
        token_id="token-1",
        subject_id="1",
        tenant_id="tenant-a",
        asset_id="1",
        account_id="deploy",
        protocol=protocol,
        connector_id="connector-1",
        expires_at=now + timedelta(minutes=5),
    )

    class FakeTokenStore:
        async def consume(self, token_id: str, now: object) -> ConnectionToken:
            assert token_id == "token-1"
            return token

    class CaptureScheduler:
        def __init__(self) -> None:
            self.requests: list = []

        async def dispatch(self, request) -> dict:  # noqa: ANN001
            self.requests.append(request)
            if reason is not None:
                raise reason
            return {
                "connector_session_id": "cs-k8s",
                "connection_url": "connector-runtime://cs-k8s",
            }

        async def release(self, connector_session_id: str) -> None:
            return None

    used = scheduler or CaptureScheduler()
    service = SessionGatewayService(
        policy_client=AllowPolicy(),
        token_store=FakeTokenStore(),
        connector_scheduler=used,
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
    body = json_body or {
        "asset_id": "1",
        "account_id": "deploy",
        "protocol": protocol,
        "connection_token": "token-1",
    }
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/sessions/", json=body)
    finally:
        app.dependency_overrides.clear()
    return response, used


def test_k8s_session_create_passes_chosen_pod_and_optional_container() -> None:
    response, scheduler = _post_session(
        protocol="k8s",
        scheduler=None,
        json_body={
            "asset_id": "1",
            "account_id": "deploy",
            "protocol": "k8s",
            "connection_token": "token-1",
            "pod": "web-0",
            "container": "app",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["protocol"] == "k8s"
    dispatched = scheduler.requests[0]
    assert dispatched.protocol == "k8s"
    assert dispatched.pod == "web-0"
    assert dispatched.container == "app"


def test_k8s_session_create_allows_empty_container() -> None:
    response, scheduler = _post_session(
        protocol="k8s",
        scheduler=None,
        json_body={
            "asset_id": "1",
            "account_id": "deploy",
            "protocol": "k8s",
            "connection_token": "token-1",
            "pod": "web-0",
        },
    )
    assert response.status_code == 201
    assert scheduler.requests[0].pod == "web-0"
    assert scheduler.requests[0].container == ""


@pytest.mark.parametrize(
    "code,copy",
    [
        ("K8S_HTTPS_CA_REQUIRED", "无法连接（需要 HTTPS 和 CA）"),
        ("K8S_INSECURE_TRANSPORT", "无法连接（需要 HTTPS 和 CA）"),
        ("K8S_TLS_CA_MISSING", "无法连接（需要 HTTPS 和 CA）"),
        ("K8S_NAMESPACE_FORBIDDEN", "无法连接"),
        ("K8S_NAMESPACE_MISSING", "无法连接"),
        ("K8S_POD_REQUIRED", "无法连接"),
        ("K8S_TARGET_INCOMPLETE", "无法连接"),
        ("CONNECT_METHOD_ACL_REJECTED", "无法连接"),
        ("HOST_KEY_UNAPPROVED", "无法连接"),
    ],
)
def test_session_api_maps_k8s_and_ssh_denies_to_chinese_copy(code: str, copy: str) -> None:
    response, _ = _post_session(
        protocol="k8s" if code.startswith("K8S") or code == "CONNECT_METHOD_ACL_REJECTED" else "ssh",
        scheduler=None,
        reason=K8sChannelError(code, "internal") if code.startswith("K8S") else PermissionError(code),
        json_body={
            "asset_id": "1",
            "account_id": "deploy",
            "protocol": "k8s" if code.startswith("K8S") or code == "CONNECT_METHOD_ACL_REJECTED" else "ssh",
            "connection_token": "token-1",
            "pod": "web-0",
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert body["message"] == copy
    assert "没有权限" not in str(body)
    assert "越权" not in str(body)


async def test_ssh_host_key_path_still_fail_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        platform = Platform(name="Linux", category="host", protocols='["ssh"]')
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
                protocol="ssh",
                secret_id="sec_k8s",
                status="active",
            )
        )
        await session.commit()
        await session.refresh(asset)
        asset_id = asset.id
    resolver, scanner = _resolver(session_factory)
    with pytest.raises(PermissionError, match="HOST_KEY_UNAPPROVED"):
        await resolver.resolve(
            ConnectorDispatchRequest(
                session_id="sess-ssh",
                connector_id="conn-1",
                tenant_id="tenant-a",
                subject_id="user-1",
                asset_id=str(asset_id),
                account_id="root",
                protocol="ssh",
            )
        )
    assert scanner.calls == 1
    assert HostKeyPresentation.APPROVED.value == "approved"
