# ruff: noqa: E402, I001
"""#t69：连接器会话运行时与网关接线测试。

- 运行时/调度器用例基于进程内 asyncssh 服务器，验证 dispatch 打开真实通道、release 关闭。
- 网关集成用例把 ConnectorRuntimeScheduler 装进 SessionGatewayService，验证完整
  create_session → ACTIVE 会真正建立连接器会话，close 会释放它。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncssh
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sessions.service import (
    ConnectorDispatchRequest,
    InMemoryConnectionTokenStore,
    JitGrantSessionBinding,
    SessionGatewayService,
    SessionStatus,
)
from app.connectors.session_runtime import (
    ConnectorRuntimeScheduler,
    ConnectorSessionMode,
    ConnectorSessionRuntime,
    InMemorySessionConnectionResolver,
    SessionConnectionSpec,
)
from app.connectors.ssh_channel import CommandEvent, SshChannel, SshCredential, SshTarget


@dataclass
class _RecordingSink:
    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


async def _echo_handler(process: asyncssh.SSHServerProcess) -> None:
    process.stdout.write(f"executed:{process.command or '<login>'}")
    process.exit(0)


@dataclass
class _RunningServer:
    acceptor: asyncssh.SSHAcceptor
    host: str
    port: int
    host_public_key: str
    client_private_key: bytes


@pytest.fixture
async def server() -> AsyncIterator[_RunningServer]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = asyncssh.import_authorized_keys(client_key.export_public_key().decode())
    acceptor = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=_echo_handler,
    )
    running = _RunningServer(
        acceptor=acceptor,
        host="127.0.0.1",
        port=acceptor.get_port(),
        host_public_key=host_key.export_public_key().decode().strip(),
        client_private_key=client_key.export_private_key(),
    )
    try:
        yield running
    finally:
        running.acceptor.close()
        await running.acceptor.wait_closed()


def _resolver_for(
    server: _RunningServer,
    *,
    mode: ConnectorSessionMode,
    tenant_id: str = "default",
) -> InMemorySessionConnectionResolver:
    resolver = InMemorySessionConnectionResolver()
    resolver.register(
        tenant_id=tenant_id,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        spec=SessionConnectionSpec(
            mode=mode,
            target=SshTarget(
                host=server.host,
                port=server.port,
                username="janus",
                trusted_host_key=server.host_public_key,
            ),
            credential=SshCredential(private_key=server.client_private_key),
        ),
    )
    return resolver


def _dispatch_request(
    session_id: str = "sess-1", *, tenant_id: str = "default"
) -> ConnectorDispatchRequest:
    return ConnectorDispatchRequest(
        session_id=session_id,
        connector_id="conn-1",
        tenant_id=tenant_id,
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
    )


# --- 运行时 / 调度器：dispatch 打开真实通道，release 关闭 -------------------------


async def test_runtime_opens_real_exec_channel_and_closes(
    server: _RunningServer,
    acl_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC),
        session_factory=acl_session_factory,
        id_factory=lambda: "cs-fixed",
    )

    record = await runtime.open(_dispatch_request())
    assert record.connector_session_id == "cs-fixed"
    assert record.mode is ConnectorSessionMode.EXEC
    assert runtime.active_ids() == ["cs-fixed"]

    # 通道是真实可用的：跑一条命令。
    sink = _RecordingSink()
    assert isinstance(record.channel, SshChannel)
    event = await record.channel.run_command("whoami", sink, sequence=0)
    assert event.output_excerpt == "executed:whoami"

    await runtime.close("cs-fixed")
    assert runtime.active_ids() == []


async def test_scheduler_dispatch_and_release(server: _RunningServer) -> None:
    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC),
        id_factory=lambda: "cs-9",
    )
    scheduler = ConnectorRuntimeScheduler(runtime)

    result = await scheduler.dispatch(_dispatch_request())
    assert result["connector_session_id"] == "cs-9"
    assert result["connection_url"] == "connector-runtime://cs-9"
    assert runtime.active_ids() == ["cs-9"]

    await scheduler.release("cs-9")
    assert runtime.active_ids() == []


async def test_runtime_unresolved_target_raises(server: _RunningServer) -> None:
    from app.connectors.ssh_channel import SshChannelError

    runtime = ConnectorSessionRuntime(InMemorySessionConnectionResolver())
    with pytest.raises(SshChannelError) as exc_info:
        await runtime.open(_dispatch_request())
    assert exc_info.value.code == "CONNECTOR_TARGET_UNRESOLVED"


async def test_interactive_mode_requires_command_sink(server: _RunningServer) -> None:
    from app.connectors.ssh_channel import SshChannelError

    runtime = ConnectorSessionRuntime(_resolver_for(server, mode=ConnectorSessionMode.INTERACTIVE))
    with pytest.raises(SshChannelError) as exc_info:
        await runtime.open(_dispatch_request())
    assert exc_info.value.code == "CONNECTOR_COMMAND_SINK_MISSING"


# --- 网关集成：create_session → ACTIVE 建立真实连接器会话，close 释放 -------------


@dataclass
class _Grant:
    status: str = "active"
    subject_id: str = "user-1"
    asset_id: str = "asset-1"
    account_id: str = "root"
    protocol: str = "ssh"
    action: str = "session.connect"
    connector_id: str = "conn-1"
    workflow_request_id: str = "wf-1"
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=10))


class _AllowJitGrantClient:
    async def get_grant(self, grant_id: str, *, tenant_id: str) -> _Grant:
        return _Grant()

    async def validate_for_session(self, **_kwargs: object) -> JitGrantSessionBinding:
        return JitGrantSessionBinding(
            jit_grant_id="grant-1",
            workflow_request_id="wf-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )

    async def mark_session_bound(self, **_kwargs: object) -> None:
        return None


class _AllowPolicyClient:
    async def evaluate(self, request: dict) -> dict:
        return {"decision": "allow", "reason_code": "OK", "ttl_seconds": 300, "obligations": []}


async def test_gateway_create_session_opens_and_close_releases_connector(
    server: _RunningServer,
) -> None:
    resolver = _resolver_for(server, mode=ConnectorSessionMode.EXEC)
    runtime = ConnectorSessionRuntime(resolver, id_factory=lambda: "cs-live")
    scheduler = ConnectorRuntimeScheduler(runtime)
    service = SessionGatewayService(
        policy_client=_AllowPolicyClient(),
        token_store=InMemoryConnectionTokenStore(),
        connector_scheduler=scheduler,
        jit_grant_client=_AllowJitGrantClient(),
    )

    issue = await service.issue_connection_token(
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        jit_grant_id="grant-1",
    )
    session = await service.create_session(
        subject_id="user-1",
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        connection_token=issue.connection_token,
        jit_grant_id="grant-1",
    )

    # 会话进入 ACTIVE，且真的建立了连接器侧会话。
    assert session.status is SessionStatus.ACTIVE
    assert session.connector_session_id == "cs-live"
    assert session.connection_url == "connector-runtime://cs-live"
    assert runtime.active_ids() == ["cs-live"]

    closed = await service.close_session(
        session_id=session.id, subject_id="user-1", reason="done"
    )
    assert closed.status is SessionStatus.CLOSED
    # 网关关闭连带释放连接器侧会话。
    assert runtime.active_ids() == []


from app.connectors.command_policy import CommandPolicyGuard, InMemoryCommandAuditSink
from app.connectors.ssh_channel import SshChannelError
from app.models.acl import CommandFilterAction
from app.policy.schemas import (
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingResponse,
    ResourceRef,
    SubjectRef,
)


class _DenyPolicy:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def evaluate_command(self, request):
        self.seen.append(request.command)
        return CommandDecisionResponse(
            effect=CommandFilterEffect.DENY,
            action=CommandFilterAction.REJECT,
            reason_code="COMMAND_REJECT",
            explain_trace=["test"],
            audit_event_id="pde_test",
        )

    def mask(self, request):
        return MaskingResponse(
            masked_text=request.text,
            redaction_count=0,
            explain_trace=[],
            audit_event_id="pde_mask",
        )


async def test_runtime_injected_guard_deny_does_not_reach_remote(
    server: _RunningServer,
) -> None:
    audit = InMemoryCommandAuditSink()
    policy = _DenyPolicy()
    guard = CommandPolicyGuard(
        policy,
        subject=SubjectRef(id="user-1", tenant_id="default"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="default"),
        account_id="root",
        audit_sink=audit,
    )
    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC),
        command_policy=guard,
        id_factory=lambda: "cs-deny",
    )
    record = await runtime.open(_dispatch_request())
    sink = _RecordingSink()
    with pytest.raises(SshChannelError) as excinfo:
        await record.channel.run_command("rm -rf /", sink, sequence=0)
    assert excinfo.value.code == "SSH_COMMAND_DENIED"
    assert excinfo.value.audit_event_id == audit.events[0]["id"]
    assert sink.events == []
    assert policy.seen == ["rm -rf /"]
    await runtime.close("cs-deny")



from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app


@pytest.fixture
async def acl_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


def _install_acl_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncIterator[AsyncSession]:
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


def _seed_reject_rm_via_crud(*, tenant_id: str = "tenant-a") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": ["admin"],
    }
    with TestClient(app) as client:
        created = client.post(
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
    assert created.status_code == 201, created.text


async def test_runtime_default_assembly_tenant_acl_does_not_reach_remote(
    server: _RunningServer,
    acl_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _install_acl_db(acl_session_factory)
    _seed_reject_rm_via_crud(tenant_id="tenant-a")
    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC, tenant_id="tenant-a"),
        session_factory=acl_session_factory,
        id_factory=lambda: "cs-acl",
    )
    record = await runtime.open(_dispatch_request(tenant_id="tenant-a"))
    sink = _RecordingSink()
    with pytest.raises(SshChannelError) as excinfo:
        await record.channel.run_command("rm -rf /", sink, sequence=0)
    assert excinfo.value.code == "SSH_COMMAND_DENIED"
    assert sink.events == []
    allowed = await record.channel.run_command("whoami", sink, sequence=1)
    assert allowed.output_excerpt == "executed:whoami"
    await runtime.close("cs-acl")


async def test_runtime_unavailable_store_does_not_reach_remote(server: _RunningServer) -> None:
    def boom(*_args, **_kwargs):  # noqa: ANN001
        raise TimeoutError("db timed out")

    runtime = ConnectorSessionRuntime(
        _resolver_for(server, mode=ConnectorSessionMode.EXEC),
        session_factory=boom,
        id_factory=lambda: "cs-down",
    )
    record = await runtime.open(_dispatch_request())
    sink = _RecordingSink()
    with pytest.raises(SshChannelError) as excinfo:
        await record.channel.run_command("whoami", sink, sequence=0)
    assert excinfo.value.code == "SSH_COMMAND_DENIED"
    assert excinfo.value.detail == "COMMAND_POLICY_STORE_UNAVAILABLE"
    assert sink.events == []
    await runtime.close("cs-down")
