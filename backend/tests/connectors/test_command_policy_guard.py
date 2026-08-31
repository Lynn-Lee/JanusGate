"""#t65：执行前 command_policy 守卫单元测试（无 live SSH）。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.connectors.command_policy import (
    CommandPolicyGuard,
    InMemoryCommandAuditSink,
    UnavailablePolicyStore,
    command_sha256,
    default_command_policy_guard,
    load_tenant_policy_service,
)
from app.policy.schemas import (
    CommandDecisionRequest,
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingResponse,
    ResourceRef,
    SubjectRef,
)
from app.models.acl import CommandFilterAction


@dataclass
class _FakePolicy:
    effect: CommandFilterEffect = CommandFilterEffect.ALLOW
    action: str = CommandFilterAction.ACCEPT
    reason_code: str = "COMMAND_ACCEPTED_BY_DEFAULT"
    raise_on_eval: bool = False
    masked_text: str | None = None

    def evaluate_command(self, request):  # noqa: ANN001
        if self.raise_on_eval:
            raise RuntimeError("evaluator crashed")
        return CommandDecisionResponse(
            effect=self.effect,
            action=self.action,
            reason_code=self.reason_code,
            explain_trace=["fake"],
            audit_event_id="pde_fake",
        )

    def mask(self, request):  # noqa: ANN001
        text = self.masked_text if self.masked_text is not None else request.text
        return MaskingResponse(
            masked_text=text,
            redaction_count=int(text != request.text),
            applied_rule_ids=["rule-1"] if text != request.text else [],
            explain_trace=["fake-mask"],
            audit_event_id="pde_mask",
        )


def _guard(policy: _FakePolicy, sink: InMemoryCommandAuditSink | None = None) -> CommandPolicyGuard:
    return CommandPolicyGuard(
        policy,
        subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-a"),
        account_id="acct-1",
        audit_sink=sink,
        session_id="sess-1",
    )


@pytest.mark.asyncio
async def test_no_acl_default_allow() -> None:
    sink = InMemoryCommandAuditSink()
    decision = await _guard(_FakePolicy(), sink).authorize("ls")
    assert decision.allowed is True
    assert decision.effect is CommandFilterEffect.ALLOW
    assert sink.events == []


@pytest.mark.asyncio
async def test_reject_blocks_and_audits_without_plaintext() -> None:
    sink = InMemoryCommandAuditSink()
    command = "rm -rf /"
    decision = await _guard(
        _FakePolicy(
            effect=CommandFilterEffect.DENY,
            action=CommandFilterAction.REJECT,
            reason_code="COMMAND_REJECT",
        ),
        sink,
    ).authorize(command)
    assert decision.allowed is False
    assert decision.audit_event_id
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["id"] == decision.audit_event_id
    assert event["command_sha256"] == command_sha256(command)
    assert command not in str(event)
    assert "password" not in str(event).lower()


@pytest.mark.asyncio
async def test_review_treated_as_deny_until_t74() -> None:
    sink = InMemoryCommandAuditSink()
    decision = await _guard(
        _FakePolicy(
            effect=CommandFilterEffect.REVIEW,
            action=CommandFilterAction.REVIEW,
            reason_code="COMMAND_REVIEW",
        ),
        sink,
    ).authorize("sudo su")
    assert decision.allowed is False
    assert decision.effect is CommandFilterEffect.DENY
    assert sink.events[0]["reason_code"] == "COMMAND_REVIEW"


@pytest.mark.asyncio
async def test_evaluate_failure_is_fail_closed_and_audited() -> None:
    sink = InMemoryCommandAuditSink()
    decision = await _guard(_FakePolicy(raise_on_eval=True), sink).authorize("id")
    assert decision.allowed is False
    assert decision.reason_code == "COMMAND_EVALUATE_FAILED"
    assert sink.events[0]["id"] == decision.audit_event_id


def test_mask_text_applies_cumulative_placeholder() -> None:
    guard = _guard(_FakePolicy(masked_text="user=***"))
    assert guard.mask_text("user=alice") == "user=***"


from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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


@pytest.mark.asyncio
async def test_default_guard_allows_when_tenant_has_no_acl(
    acl_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sink = InMemoryCommandAuditSink()
    guard = await default_command_policy_guard(
        subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-a"),
        account_id="root",
        audit_sink=sink,
        session_factory=acl_session_factory,
    )
    decision = await guard.authorize("rm -rf /")
    assert decision.allowed is True
    assert sink.events == []


@pytest.mark.asyncio
async def test_default_guard_loads_tenant_crud_reject(
    acl_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _install_acl_db(acl_session_factory)
    _seed_reject_rm_via_crud(tenant_id="tenant-a")
    sink = InMemoryCommandAuditSink()
    guard = await default_command_policy_guard(
        subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-a"),
        account_id="root",
        audit_sink=sink,
        session_factory=acl_session_factory,
    )
    denied = await guard.authorize("rm -rf /")
    allowed = await guard.authorize("ls")
    assert denied.allowed is False
    assert denied.reason_code == "COMMAND_REJECT"
    assert allowed.allowed is True
    assert len(sink.events) == 1



def _boom_factory(exc: BaseException):
    def factory(*_args, **_kwargs):  # noqa: ANN001
        raise exc

    return factory


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("refused"),
        TimeoutError("timed out"),
        OSError("down"),
    ],
)
@pytest.mark.asyncio
async def test_unavailable_policy_store_denies_and_audits(exc: BaseException) -> None:
    sink = InMemoryCommandAuditSink()
    guard = await default_command_policy_guard(
        subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-a"),
        account_id="root",
        audit_sink=sink,
        session_factory=_boom_factory(exc),
    )
    decision = await guard.authorize("ls")
    assert isinstance(guard._policy, UnavailablePolicyStore)
    assert decision.allowed is False
    assert decision.reason_code == "COMMAND_POLICY_STORE_UNAVAILABLE"
    assert len(sink.events) == 1
    assert sink.events[0]["id"] == decision.audit_event_id


@pytest.mark.asyncio
async def test_sqlalchemy_operational_error_is_fail_closed() -> None:
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    for exc in (
        OperationalError("connect", {}, OSError("refused")),
        InterfaceError("connect", {}, OSError("refused")),
        DBAPIError("connect", {}, OSError("refused")),
    ):
        policy = await load_tenant_policy_service(
            tenant_id="tenant-a", session_factory=_boom_factory(exc)
        )
        assert isinstance(policy, UnavailablePolicyStore)
        denied = policy.evaluate_command(
            CommandDecisionRequest(
                subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
                resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-a"),
                account_id="root",
                command="whoami",
            )
        )
        assert denied.effect is CommandFilterEffect.DENY
        assert denied.reason_code == "COMMAND_POLICY_STORE_UNAVAILABLE"
