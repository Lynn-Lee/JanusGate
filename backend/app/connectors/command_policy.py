"""连接器侧命令策略守卫（#t65：执行前接线）。

在 SSH exec/PTY 与 K8s exec **落到远端之前**调用
:meth:`~app.policy.decision.PolicyDecisionService.evaluate_command`。连接器不得自行判定。

- ``DENY`` 不落远程并写 #t61；``REVIEW`` 开/复用工单并返回 ``command_review_pending``（终端「命令待复核」）
- ``evaluate_command`` 抛错 fail-closed
- 无 ACL 命中仍 ALLOW（deny-overlay，沿用判定服务语义）
- 审计元数据只带 ``command_sha256``，不落明文命令 / 密钥
"""

from __future__ import annotations

import hashlib
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.policy.schemas import (
    CommandDecisionRequest,
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingRequest,
    MaskingResponse,
    ResourceRef,
    SubjectRef,
)


class CommandPolicyEvaluator(Protocol):
    """判定服务最小协议，便于测试注入 Fake。"""

    def evaluate_command(self, request: CommandDecisionRequest) -> Any: ...

    def mask(self, request: MaskingRequest) -> Any: ...


class CommandAuditSink(Protocol):
    """#t61 拒绝审计落盘。实现不得持久化明文命令。"""

    async def record_command_denied(
        self,
        *,
        actor: dict[str, Any],
        reason_code: str,
        effect: str,
        resource_id: str,
        command_sha256: str,
        policy_audit_event_id: str,
        session_id: str | None = None,
    ) -> str:
        """写入持久化审计，返回可回查的 ``audit_event_id``。"""
        ...


@dataclass(frozen=True)
class CommandPolicyDecision:
    """单条命令的执行前判定结果。"""

    allowed: bool
    effect: CommandFilterEffect
    reason_code: str
    audit_event_id: str
    action: str = ""
    user_message: str = ""
    workflow_request_id: str = ""


class CommandReviewBroker(Protocol):
    """#t74：REVIEW 命中时开/复用工单，并消费一次性放行 grant。"""

    async def consume_allow(
        self,
        *,
        subject_id: str,
        tenant_id: str,
        asset_id: str,
        command: str,
        session_id: str | None,
    ) -> bool:
        """若存在未过期的一次性放行则消费并返回 True。"""
        ...

    async def open_or_reuse_ticket(
        self,
        *,
        actor: dict[str, Any],
        asset_id: str,
        account_id: str,
        protocol: str,
        command: str,
        session_id: str | None,
        reviewer_subject_ids: list[str],
    ) -> str:
        """创建或复用进行中工单，返回 workflow_request_id。"""
        ...


class InMemoryCommandAuditSink:
    """测试用内存审计槽；记录拒绝事件且不含明文命令。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record_command_denied(
        self,
        *,
        actor: dict[str, Any],
        reason_code: str,
        effect: str,
        resource_id: str,
        command_sha256: str,
        policy_audit_event_id: str,
        session_id: str | None = None,
    ) -> str:
        event_id = f"aud_{uuid4().hex}"
        self.events.append(
            {
                "id": event_id,
                "actor_id": str(actor.get("id") or ""),
                "tenant_id": str(actor.get("tenant_id") or ""),
                "event_type": "session.command.rejected",
                "reason_code": reason_code,
                "effect": effect,
                "resource_id": resource_id,
                "command_sha256": command_sha256,
                "policy_audit_event_id": policy_audit_event_id,
                "session_id": session_id,
            }
        )
        return event_id



class AuditServiceCommandAuditSink:
    """生产 #t61 审计槽：拒绝写入 ``audit_events``，返回可回查 ``audit_event_id``。"""

    def __init__(self, service: Any | None = None) -> None:
        if service is None:
            from app.api.audits.service import audit_service as default_service

            service = default_service
        self._service = service

    async def record_command_denied(
        self,
        *,
        actor: dict[str, Any],
        reason_code: str,
        effect: str,
        resource_id: str,
        command_sha256: str,
        policy_audit_event_id: str,
        session_id: str | None = None,
    ) -> str:
        from app.api.audits.schemas import AuditCategory, AuditEventCreate, AuditSeverity

        event = await self._service.create_event(
            AuditEventCreate(
                event_type="session.command.rejected",
                category=AuditCategory.policy,
                action="command.reject",
                resource_type="session_command",
                resource_id=resource_id or "unknown",
                session_id=session_id,
                severity=AuditSeverity.high,
                message="Command rejected by command-filter ACL",
                metadata={
                    "reason_code": reason_code,
                    "effect": effect,
                    "command_sha256": command_sha256,
                    "policy_audit_event_id": policy_audit_event_id,
                },
            ),
            actor,
        )
        return event.id


class UnavailablePolicyStore:
    """库不可用时的 fail-closed 判定器：任何命令 DENY，不 overlay 放行。"""

    def evaluate_command(self, request: CommandDecisionRequest) -> CommandDecisionResponse:
        return CommandDecisionResponse(
            effect=CommandFilterEffect.DENY,
            action="reject",
            reason_code="COMMAND_POLICY_STORE_UNAVAILABLE",
            explain_trace=["policy_store_unavailable"],
            audit_event_id=f"pde_{uuid4().hex}",
        )

    def mask(self, request: MaskingRequest) -> MaskingResponse:
        return MaskingResponse(
            masked_text=request.text,
            redaction_count=0,
            explain_trace=["policy_store_unavailable"],
            audit_event_id=f"pde_{uuid4().hex}",
        )


def _is_policy_store_unavailable(exc: BaseException) -> bool:
    """识别「库连不上」；判定逻辑本身的错误不得被当成库不可用。"""

    if isinstance(exc, (OSError, ConnectionError, TimeoutError)):
        return True
    module = type(exc).__module__ or ""
    name = type(exc).__name__
    if module.startswith("sqlalchemy") and name in {
        "OperationalError",
        "InterfaceError",
        "DBAPIError",
        "TimeoutError",
    }:
        return True
    orig = getattr(exc, "orig", None)
    return orig is not None and isinstance(orig, (OSError, ConnectionError, TimeoutError))


async def load_tenant_policy_service(
    *,
    tenant_id: str,
    user_id: str = "unknown",
    db: AsyncSession | None = None,
    session_factory: Any | None = None,
) -> Any:
    """按租户从库装载命令过滤 ACL + 脱敏规则。

    无记录时返回空服务（overlay 放行）。库连不上返回
    :class:`UnavailablePolicyStore`（fail-closed DENY），不得 overlay 放行。
    """

    from app.policy.repository import build_tenant_policy_service
    from app.tenancy.scope import ActorScope

    scope = ActorScope(user_id=user_id, tenant_id=tenant_id)
    try:
        if db is not None:
            return await build_tenant_policy_service(db, scope)
        factory = session_factory
        if factory is None:
            from app.core.database import AsyncSessionLocal

            factory = AsyncSessionLocal
        async with factory() as session:
            return await build_tenant_policy_service(session, scope)
    except Exception as exc:
        if _is_policy_store_unavailable(exc):
            return UnavailablePolicyStore()
        raise


async def default_command_policy_guard(
    *,
    subject: SubjectRef | None = None,
    resource: ResourceRef | None = None,
    account_id: str = "",
    policy: CommandPolicyEvaluator | None = None,
    audit_sink: CommandAuditSink | None = None,
    review_broker: CommandReviewBroker | None = None,
    session_id: str | None = None,
    db: AsyncSession | None = None,
    session_factory: Any | None = None,
) -> CommandPolicyGuard:
    """生产默认守卫：按会话租户加载 ACL；无 ACL overlay 放行；拒绝走 #t61。"""

    resolved_subject = subject or SubjectRef(id="unknown", tenant_id="default")
    resolved_resource = resource or ResourceRef(
        id="unknown", type="asset", tenant_id=resolved_subject.tenant_id
    )
    factory = session_factory
    if policy is None:
        if db is not None or factory is not None:
            policy = await load_tenant_policy_service(
                tenant_id=resolved_subject.tenant_id,
                user_id=resolved_subject.id,
                db=db,
                session_factory=factory,
            )
        else:
            from app.policy.decision import PolicyDecisionService

            # 未配置租户库（单测直连通道）时 overlay 放行；生产组装必传 session_factory。
            policy = PolicyDecisionService()
    if review_broker is None and (db is not None or factory is not None):
        review_broker = WorkflowCommandReviewBroker(db=db, session_factory=factory)
    return CommandPolicyGuard(
        policy,
        subject=resolved_subject,
        resource=resolved_resource,
        account_id=account_id or "*",
        audit_sink=audit_sink or AuditServiceCommandAuditSink(),
        review_broker=review_broker,
        session_id=session_id,
    )


class CommandPolicyGuard:
    """执行前守卫：evaluate → 拒绝则审计并阻断；放行后可对输出 mask。"""

    def __init__(
        self,
        policy: CommandPolicyEvaluator,
        *,
        subject: SubjectRef,
        resource: ResourceRef,
        account_id: str,
        audit_sink: CommandAuditSink | None = None,
        review_broker: CommandReviewBroker | None = None,
        actor: dict[str, Any] | None = None,
        session_id: str | None = None,
        protocol: str = "",
    ) -> None:
        self._policy = policy
        self._subject = subject
        self._resource = resource
        self._account_id = account_id
        self._audit_sink = audit_sink
        self._review_broker = review_broker
        self._actor = actor or {
            "id": subject.id,
            "username": subject.id,
            "tenant_id": subject.tenant_id,
        }
        self._session_id = session_id
        self._protocol = protocol or str(getattr(resource, "type", "") or "ssh")

    async def authorize(self, command: str) -> CommandPolicyDecision:
        """执行前判定。不允许时调用方不得向远端发送该命令。"""

        from app.workflows.command_review import (
            COMMAND_REVIEW_PENDING,
            USER_MSG_PENDING,
        )

        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()

        # One-shot allow grant (approved command_review) — check before ACL deny.
        if self._review_broker is not None:
            try:
                consumed = await self._review_broker.consume_allow(
                    subject_id=self._subject.id,
                    tenant_id=self._subject.tenant_id,
                    asset_id=self._resource.id,
                    command=command,
                    session_id=self._session_id,
                )
            except Exception:
                consumed = False
            if consumed:
                return CommandPolicyDecision(
                    allowed=True,
                    effect=CommandFilterEffect.ALLOW,
                    reason_code="COMMAND_REVIEW_GRANT_CONSUMED",
                    audit_event_id=f"pde_{uuid4().hex}",
                    action="accept",
                    user_message="",
                )

        try:
            response = self._policy.evaluate_command(
                CommandDecisionRequest(
                    subject=self._subject,
                    resource=self._resource,
                    account_id=self._account_id,
                    command=command,
                )
            )
        except Exception:
            return await self._deny(
                command_sha256=digest,
                effect=CommandFilterEffect.DENY,
                reason_code="COMMAND_EVALUATE_FAILED",
                action="reject",
                policy_audit_event_id=f"pde_{uuid4().hex}",
            )

        effect = response.effect
        if effect is CommandFilterEffect.REVIEW:
            workflow_request_id = ""
            if self._review_broker is not None:
                try:
                    reviewers = list(getattr(response, "reviewer_subject_ids", None) or [])
                    if not reviewers:
                        obligations = getattr(response, "obligations", None) or {}
                        raw = obligations.get("reviewer_subject_ids") if isinstance(obligations, dict) else []
                        reviewers = [str(item) for item in (raw or [])]
                    workflow_request_id = await self._review_broker.open_or_reuse_ticket(
                        actor=self._actor,
                        asset_id=self._resource.id,
                        account_id=self._account_id,
                        protocol=self._protocol,
                        command=command,
                        session_id=self._session_id,
                        reviewer_subject_ids=reviewers,
                    )
                except Exception:
                    workflow_request_id = ""
            return await self._deny(
                command_sha256=digest,
                effect=CommandFilterEffect.DENY,
                reason_code=COMMAND_REVIEW_PENDING,
                action=str(response.action),
                policy_audit_event_id=str(response.audit_event_id),
                user_message=USER_MSG_PENDING,
                workflow_request_id=workflow_request_id,
            )

        if effect is CommandFilterEffect.DENY:
            return await self._deny(
                command_sha256=digest,
                effect=CommandFilterEffect.DENY,
                reason_code=str(response.reason_code),
                action=str(response.action),
                policy_audit_event_id=str(response.audit_event_id),
            )

        return CommandPolicyDecision(
            allowed=True,
            effect=CommandFilterEffect.ALLOW,
            reason_code=str(response.reason_code),
            audit_event_id=str(response.audit_event_id),
            action=str(response.action),
        )

    def mask_text(self, text: str) -> str:
        """对可见输出做累计脱敏；失败时返回原文（入库路径仍有第二道 mask+_redact）。"""

        try:
            masked = self._policy.mask(
                MaskingRequest(
                    subject=self._subject,
                    resource=self._resource,
                    account_id=self._account_id,
                    text=text,
                )
            )
        except Exception:
            return text
        return str(masked.masked_text)

    async def _deny(
        self,
        *,
        command_sha256: str,
        effect: CommandFilterEffect,
        reason_code: str,
        action: str,
        policy_audit_event_id: str,
        user_message: str = "",
        workflow_request_id: str = "",
    ) -> CommandPolicyDecision:
        sink = self._audit_sink or AuditServiceCommandAuditSink()
        audit_event_id = await sink.record_command_denied(
            actor=self._actor,
            reason_code=reason_code,
            effect=str(effect),
            resource_id=self._resource.id,
            command_sha256=command_sha256,
            policy_audit_event_id=policy_audit_event_id,
            session_id=self._session_id,
        )
        return CommandPolicyDecision(
            allowed=False,
            effect=effect,
            reason_code=reason_code,
            audit_event_id=audit_event_id,
            action=action,
            user_message=user_message,
            workflow_request_id=workflow_request_id,
        )


def command_sha256(command: str) -> str:
    """命令指纹，供审计对照；明文不得进入审计。"""

    return hashlib.sha256(command.encode("utf-8")).hexdigest()




class WorkflowCommandReviewBroker:
    """SQLAlchemy-backed command_review ticket + one-shot allow broker."""

    def __init__(
        self,
        *,
        db: AsyncSession | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._db = db
        self._session_factory = session_factory

    async def consume_allow(
        self,
        *,
        subject_id: str,
        tenant_id: str,
        asset_id: str,
        command: str,
        session_id: str | None,
    ) -> bool:
        async with self._session_scope() as session:
            service = self._build_service(session)
            grant = await service.consume_command_allow_grant(
                tenant_id=tenant_id,
                subject_id=subject_id,
                asset_id=asset_id,
                command=command,
                session_id=session_id,
            )
            return grant is not None

    async def open_or_reuse_ticket(
        self,
        *,
        actor: dict[str, Any],
        asset_id: str,
        account_id: str,
        protocol: str,
        command: str,
        session_id: str | None,
        reviewer_subject_ids: list[str],
    ) -> str:
        async with self._session_scope() as session:
            service = self._build_service(session)
            request = await service.ensure_command_review_ticket(
                actor=actor,
                asset_id=asset_id,
                account_id=account_id,
                protocol=protocol,
                command=command,
                session_id=session_id,
                reviewer_subject_ids=reviewer_subject_ids,
            )
            return str(request.id)

    def _build_service(self, session: AsyncSession) -> Any:
        from app.api.workflows.service import SQLAlchemyWorkflowStore, WorkflowService
        from app.workflows.ticket_flows import TicketFlowRepository, TicketFlowSnapshot

        repo = TicketFlowRepository(session)

        async def loader(
            tenant_id: str, flow_type: str = "asset_grant"
        ) -> TicketFlowSnapshot | None:
            return await repo.get_enabled_flow(tenant_id=tenant_id, flow_type=flow_type)

        return WorkflowService(
            store=SQLAlchemyWorkflowStore(session),
            ticket_flow_loader=loader,
        )

    def _session_scope(self) -> AbstractAsyncContextManager[AsyncSession]:
        if self._db is not None:
            return _PassthroughAsyncSession(self._db)
        factory = self._session_factory
        if factory is None:
            from app.core.database import AsyncSessionLocal

            factory = AsyncSessionLocal
        return factory()


class _PassthroughAsyncSession:
    """Use an externally owned AsyncSession without closing it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        return None
