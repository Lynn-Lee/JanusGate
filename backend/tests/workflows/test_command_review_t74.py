"""#t74 command_review TicketFlow wiring tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.workflows.service import (
    InMemoryWorkflowStore,
    JitGrantStatus,
    WorkflowRequestStatus,
    WorkflowService,
)
from app.connectors.command_policy import CommandPolicyGuard, InMemoryCommandAuditSink
from app.models.acl import CommandFilterAction
from app.policy.schemas import (
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingResponse,
    ResourceRef,
    SubjectRef,
)
from app.workflows.command_review import (
    COMMAND_ALLOW_ACTION,
    COMMAND_REVIEW_ACTION,
    COMMAND_REVIEW_PENDING,
    COMMAND_REVIEW_TTL_SECONDS,
    USER_MSG_PENDING,
)
from app.workflows.ticket_flows import ApprovalLevelSnapshot, TicketFlowSnapshot


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


class _FakePolicy:
    def __init__(
        self,
        *,
        effect: CommandFilterEffect = CommandFilterEffect.REVIEW,
        reviewers: list[str] | None = None,
    ) -> None:
        self.effect = effect
        self.reviewers = reviewers or []

    def evaluate_command(self, request):  # noqa: ANN001
        return CommandDecisionResponse(
            effect=self.effect,
            action=CommandFilterAction.REVIEW
            if self.effect is CommandFilterEffect.REVIEW
            else CommandFilterAction.REJECT,
            reason_code="COMMAND_REVIEW"
            if self.effect is CommandFilterEffect.REVIEW
            else "COMMAND_REJECT",
            reviewer_subject_ids=list(self.reviewers),
            obligations={"reviewer_subject_ids": list(self.reviewers)},
            explain_trace=["fake"],
            audit_event_id="pde_fake",
        )

    def mask(self, request):  # noqa: ANN001
        return MaskingResponse(
            masked_text=request.text,
            redaction_count=0,
            explain_trace=["fake"],
            audit_event_id="pde_mask",
        )


class _ServiceBroker:
    def __init__(self, service: WorkflowService) -> None:
        self._service = service

    async def consume_allow(self, **kwargs):  # noqa: ANN003
        grant = await self._service.consume_command_allow_grant(**kwargs)
        return grant is not None

    async def open_or_reuse_ticket(self, **kwargs):  # noqa: ANN003
        request = await self._service.ensure_command_review_ticket(**kwargs)
        return request.id


def _service(**kwargs) -> WorkflowService:
    return WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: "wr-cmd-1",
        grant_id_factory=lambda: "grant-cmd-1",
        **kwargs,
    )


def _flow(levels: list[list[str]]) -> TicketFlowSnapshot:
    return TicketFlowSnapshot(
        id="tf-cmd-1",
        tenant_id="tenant-1",
        name="命令复核流",
        flow_type="command_review",
        enabled=True,
        levels=[
            ApprovalLevelSnapshot(level=index, approver_user_ids=users)
            for index, users in enumerate(levels, start=1)
        ],
    )


@pytest.mark.asyncio
async def test_review_opens_ticket_and_pending_copy() -> None:
    service = _service()
    broker = _ServiceBroker(service)
    sink = InMemoryCommandAuditSink()
    guard = CommandPolicyGuard(
        _FakePolicy(reviewers=["rev-1"]),
        subject=SubjectRef(id="user-1", tenant_id="tenant-1"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-1"),
        account_id="root",
        audit_sink=sink,
        review_broker=broker,
        session_id="sess-1",
        actor={"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []},
    )
    decision = await guard.authorize("rm -rf /")
    assert decision.allowed is False
    assert decision.reason_code == COMMAND_REVIEW_PENDING
    assert decision.user_message == USER_MSG_PENDING
    assert decision.workflow_request_id == "wr-cmd-1"
    ticket = await service.get_request("wr-cmd-1", tenant_id="tenant-1")
    assert ticket is not None
    assert ticket.action == COMMAND_REVIEW_ACTION
    assert ticket.status is WorkflowRequestStatus.PENDING
    assert ticket.metadata["command"] == "rm -rf /"
    assert "没有权限" not in decision.user_message


@pytest.mark.asyncio
async def test_reuse_same_subject_asset_command() -> None:
    service = _service()
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    first = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="sudo su",
        session_id="sess-1",
        reviewer_subject_ids=["rev-1"],
    )
    # second id factory would create wr-cmd-1 again; force new factory then ensure reuse
    service.request_id_factory = lambda: "wr-cmd-2"
    second = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="sudo su",
        session_id="sess-1",
        reviewer_subject_ids=["rev-1"],
    )
    assert first.id == second.id == "wr-cmd-1"


@pytest.mark.asyncio
async def test_no_flow_uses_acl_reviewers_single_step() -> None:
    service = _service()
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    ticket = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="reboot",
        session_id="sess-1",
        reviewer_subject_ids=["rev-1"],
    )
    assert ticket.total_levels == 0
    with pytest.raises(PermissionError, match="COMMAND_REVIEW_APPROVER_MISMATCH"):
        await service.approve_request(
            ticket.id,
            actor={"id": "stranger", "username": "x", "tenant_id": "tenant-1", "permissions": []},
            decision_reason="nope",
            grant_ttl_seconds=600,
        )
    approved = await service.approve_request(
        ticket.id,
        actor={"id": "rev-1", "username": "reviewer", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="ok",
        grant_ttl_seconds=600,
    )
    assert approved.status is WorkflowRequestStatus.APPROVED
    grant = await service.get_grant(approved.grant_id, tenant_id="tenant-1")
    assert grant is not None
    assert grant.action == COMMAND_ALLOW_ACTION
    assert grant.constraints["command"] == "reboot"
    assert grant.max_session_ttl_seconds == COMMAND_REVIEW_TTL_SECONDS


@pytest.mark.asyncio
async def test_no_flow_empty_reviewers_admin_only() -> None:
    service = _service()
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    ticket = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="halt",
        reviewer_subject_ids=[],
    )
    with pytest.raises(PermissionError, match="COMMAND_REVIEW_ADMIN_REQUIRED"):
        await service.approve_request(
            ticket.id,
            actor={
                "id": "rev-1",
                "username": "reviewer",
                "tenant_id": "tenant-1",
                "permissions": ["workflow:approve"],
            },
            decision_reason="no",
            grant_ttl_seconds=600,
        )
    approved = await service.approve_request(
        ticket.id,
        actor={"id": "admin-1", "username": "admin", "tenant_id": "tenant-1", "permissions": ["admin"]},
        decision_reason="ok",
        grant_ttl_seconds=600,
    )
    assert approved.status is WorkflowRequestStatus.APPROVED


@pytest.mark.asyncio
async def test_multi_level_command_review_flow() -> None:
    service = _service()
    service.set_enabled_ticket_flow(
        "tenant-1", _flow([["a1"], ["a2"]]), flow_type="command_review"
    )
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    ticket = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="mkfs",
        session_id="sess-1",
        reviewer_subject_ids=["ignored"],
    )
    assert ticket.total_levels == 2
    mid = await service.approve_request(
        ticket.id,
        actor={"id": "a1", "username": "l1", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="L1",
        grant_ttl_seconds=600,
    )
    assert mid.status is WorkflowRequestStatus.PENDING
    final = await service.approve_request(
        ticket.id,
        actor={"id": "a2", "username": "l2", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="L2",
        grant_ttl_seconds=600,
    )
    assert final.status is WorkflowRequestStatus.APPROVED
    assert final.grant_id


@pytest.mark.asyncio
async def test_approve_then_retry_allows_once_then_retickets() -> None:
    ids = iter(["wr-1", "wr-2"])
    grant_ids = iter(["g-1", "g-2"])
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: next(ids),
        grant_id_factory=lambda: next(grant_ids),
    )
    broker = _ServiceBroker(service)
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    guard = CommandPolicyGuard(
        _FakePolicy(reviewers=["rev-1"]),
        subject=SubjectRef(id="user-1", tenant_id="tenant-1"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-1"),
        account_id="root",
        audit_sink=InMemoryCommandAuditSink(),
        review_broker=broker,
        session_id="sess-1",
        actor=actor,
    )
    pending = await guard.authorize("dangerous")
    assert pending.reason_code == COMMAND_REVIEW_PENDING
    await service.approve_request(
        "wr-1",
        actor={"id": "rev-1", "username": "r", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="ok",
        grant_ttl_seconds=600,
    )
    allowed = await guard.authorize("dangerous")
    assert allowed.allowed is True
    assert allowed.reason_code == "COMMAND_REVIEW_GRANT_CONSUMED"
    grant = await service.get_grant("g-1", tenant_id="tenant-1")
    assert grant is not None
    assert grant.status is JitGrantStatus.USED
    # second attempt after consume opens a new ticket
    again = await guard.authorize("dangerous")
    assert again.allowed is False
    assert again.reason_code == COMMAND_REVIEW_PENDING
    assert again.workflow_request_id == "wr-2"


@pytest.mark.asyncio
async def test_reject_stays_blocked() -> None:
    ids = iter(["wr-cmd-1", "wr-cmd-2"])
    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        request_id_factory=lambda: next(ids),
        grant_id_factory=lambda: "grant-cmd-1",
    )
    broker = _ServiceBroker(service)
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    guard = CommandPolicyGuard(
        _FakePolicy(reviewers=["rev-1"]),
        subject=SubjectRef(id="user-1", tenant_id="tenant-1"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-1"),
        account_id="root",
        audit_sink=InMemoryCommandAuditSink(),
        review_broker=broker,
        session_id="sess-1",
        actor=actor,
    )
    await guard.authorize("wipe")
    await service.reject_request(
        "wr-cmd-1",
        actor={"id": "rev-1", "username": "r", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="no",
    )
    denied = await guard.authorize("wipe")
    assert denied.allowed is False
    assert denied.reason_code == COMMAND_REVIEW_PENDING
    assert denied.workflow_request_id == "wr-cmd-2"


async def test_accept_reject_acl_unchanged() -> None:
    sink = InMemoryCommandAuditSink()
    # FORCE allow path by using ACCEPT effect via Fake with ALLOW
    class AcceptPolicy(_FakePolicy):
        def evaluate_command(self, request):  # noqa: ANN001
            return CommandDecisionResponse(
                effect=CommandFilterEffect.ALLOW,
                action=CommandFilterAction.ACCEPT,
                reason_code="COMMAND_ACCEPT",
                explain_trace=["fake"],
                audit_event_id="pde_ok",
            )

    class RejectPolicy(_FakePolicy):
        def evaluate_command(self, request):  # noqa: ANN001
            return CommandDecisionResponse(
                effect=CommandFilterEffect.DENY,
                action=CommandFilterAction.REJECT,
                reason_code="COMMAND_REJECT",
                explain_trace=["fake"],
                audit_event_id="pde_no",
            )

    allowed = await CommandPolicyGuard(
        AcceptPolicy(),
        subject=SubjectRef(id="user-1", tenant_id="tenant-1"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-1"),
        account_id="root",
        audit_sink=sink,
    ).authorize("ls")
    assert allowed.allowed is True
    denied = await CommandPolicyGuard(
        RejectPolicy(),
        subject=SubjectRef(id="user-1", tenant_id="tenant-1"),
        resource=ResourceRef(id="asset-1", type="ssh", tenant_id="tenant-1"),
        account_id="root",
        audit_sink=sink,
    ).authorize("rm")
    assert denied.allowed is False
    assert denied.reason_code == "COMMAND_REJECT"
    assert denied.user_message == ""


@pytest.mark.asyncio
async def test_grant_expiry_opens_new_ticket() -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    clock = {"t": now}

    def _now() -> datetime:
        return clock["t"]

    service = WorkflowService(
        store=InMemoryWorkflowStore(),
        audit_sink=FakeAuditSink(),
        now=_now,
        request_id_factory=lambda: f"wr-{clock['t'].minute}-{clock['t'].second}",
        grant_id_factory=lambda: f"g-{clock['t'].minute}-{clock['t'].second}",
    )
    actor = {"id": "user-1", "username": "alice", "tenant_id": "tenant-1", "permissions": []}
    ticket = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="expire-me",
        session_id="sess-1",
        reviewer_subject_ids=["rev-1"],
    )
    approved = await service.approve_request(
        ticket.id,
        actor={"id": "rev-1", "username": "r", "tenant_id": "tenant-1", "permissions": []},
        decision_reason="ok",
        grant_ttl_seconds=600,
    )
    clock["t"] = now + timedelta(seconds=COMMAND_REVIEW_TTL_SECONDS + 1)
    consumed = await service.consume_command_allow_grant(
        tenant_id="tenant-1",
        subject_id="user-1",
        asset_id="asset-1",
        command="expire-me",
        session_id="sess-1",
    )
    assert consumed is None
    service.request_id_factory = lambda: "wr-after-expire"
    fresh = await service.ensure_command_review_ticket(
        actor=actor,
        asset_id="asset-1",
        account_id="root",
        protocol="ssh",
        command="expire-me",
        session_id="sess-1",
        reviewer_subject_ids=["rev-1"],
    )
    assert fresh.id == "wr-after-expire"
    assert approved.grant_id
