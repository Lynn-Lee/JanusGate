"""#t74 TicketFlow / ApprovalRule persistence helpers."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import (
    ApprovalRuleModel,
    TicketFlowModel,
    TicketFlowType,
    TicketStepModel,
    TicketStepStatus,
    WorkflowRequestModel,
    WorkflowRequestStatus,
)


def _new_flow_id() -> str:
    return f"tf_{uuid.uuid4().hex}"


def _new_rule_id() -> str:
    return f"ar_{uuid.uuid4().hex}"


def _new_step_id() -> str:
    return f"ts_{uuid.uuid4().hex}"


def _json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


@dataclass
class ApprovalLevelSnapshot:
    level: int
    approver_user_ids: list[str] = field(default_factory=list)


@dataclass
class TicketFlowSnapshot:
    id: str
    tenant_id: str
    name: str
    flow_type: str
    enabled: bool
    levels: list[ApprovalLevelSnapshot] = field(default_factory=list)

    @property
    def level_count(self) -> int:
        return len(self.levels)


class TicketFlowRepository:
    """CRUD + enabled-flow lookup for TicketFlows (per-type uniqueness)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_flows(self, *, tenant_id: str) -> list[TicketFlowSnapshot]:
        result = await self._session.execute(
            select(TicketFlowModel)
            .where(TicketFlowModel.tenant_id == tenant_id)
            .order_by(TicketFlowModel.created_at.asc())
        )
        flows = list(result.scalars().all())
        return [await self._snapshot(flow) for flow in flows]

    async def get_flow(self, flow_id: str, *, tenant_id: str) -> TicketFlowSnapshot | None:
        flow = await self._flow_model(flow_id, tenant_id=tenant_id)
        if flow is None:
            return None
        return await self._snapshot(flow)

    async def get_enabled_flow(
        self, *, tenant_id: str, flow_type: str
    ) -> TicketFlowSnapshot | None:
        result = await self._session.execute(
            select(TicketFlowModel).where(
                TicketFlowModel.tenant_id == tenant_id,
                TicketFlowModel.flow_type == flow_type,
                TicketFlowModel.enabled.is_(True),
            )
        )
        flow = result.scalars().first()
        if flow is None:
            return None
        return await self._snapshot(flow)

    async def get_enabled_asset_grant_flow(self, *, tenant_id: str) -> TicketFlowSnapshot | None:
        return await self.get_enabled_flow(
            tenant_id=tenant_id, flow_type=TicketFlowType.asset_grant
        )

    async def get_enabled_command_review_flow(self, *, tenant_id: str) -> TicketFlowSnapshot | None:
        return await self.get_enabled_flow(
            tenant_id=tenant_id, flow_type=TicketFlowType.command_review
        )

    def _normalize_flow_type(self, flow_type: str) -> str:
        value = str(flow_type or TicketFlowType.asset_grant).strip()
        allowed = {TicketFlowType.asset_grant, TicketFlowType.command_review}
        if value not in {str(item) for item in allowed}:
            raise ValueError("TICKET_FLOW_TYPE_INVALID")
        return value

    async def create_flow(
        self,
        *,
        tenant_id: str,
        name: str,
        enabled: bool,
        levels: list[list[str]],
        flow_type: str = TicketFlowType.asset_grant,
    ) -> TicketFlowSnapshot:
        self._validate_levels(levels)
        normalized_type = self._normalize_flow_type(flow_type)
        if enabled:
            await self._disable_enabled_flows(tenant_id=tenant_id, flow_type=normalized_type)
        flow = TicketFlowModel(
            id=_new_flow_id(),
            tenant_id=tenant_id,
            name=name.strip(),
            flow_type=normalized_type,
            enabled=enabled,
        )
        self._session.add(flow)
        await self._session.flush()
        await self._replace_rules(tenant_id=tenant_id, flow_id=flow.id, levels=levels)
        await self._session.flush()
        return await self._snapshot(flow)

    async def update_flow(
        self,
        flow_id: str,
        *,
        tenant_id: str,
        name: str,
        enabled: bool,
        levels: list[list[str]],
    ) -> TicketFlowSnapshot:
        self._validate_levels(levels)
        flow = await self._flow_model(flow_id, tenant_id=tenant_id)
        if flow is None:
            raise ValueError("TICKET_FLOW_NOT_FOUND")
        flow_type = str(getattr(flow.flow_type, "value", flow.flow_type))
        if enabled:
            await self._disable_enabled_flows(
                tenant_id=tenant_id, flow_type=flow_type, except_id=flow.id
            )
        flow.name = name.strip()
        flow.enabled = enabled
        # flow_type is immutable after create
        await self._replace_rules(tenant_id=tenant_id, flow_id=flow.id, levels=levels)
        await self._session.flush()
        return await self._snapshot(flow)

    async def delete_flow(self, flow_id: str, *, tenant_id: str) -> None:
        flow = await self._flow_model(flow_id, tenant_id=tenant_id)
        if flow is None:
            raise ValueError("TICKET_FLOW_NOT_FOUND")
        if await self._has_in_progress_tickets(flow_id=flow.id, tenant_id=tenant_id):
            raise ValueError("TICKET_FLOW_IN_PROGRESS")
        rules = await self._session.execute(
            select(ApprovalRuleModel).where(
                ApprovalRuleModel.ticket_flow_id == flow.id,
                ApprovalRuleModel.tenant_id == tenant_id,
            )
        )
        for rule in rules.scalars().all():
            await self._session.delete(rule)
        await self._session.delete(flow)
        await self._session.flush()

    async def create_steps_for_request(
        self,
        *,
        tenant_id: str,
        workflow_request_id: str,
        flow: TicketFlowSnapshot,
    ) -> list[TicketStepModel]:
        steps: list[TicketStepModel] = []
        for level in flow.levels:
            status = (
                TicketStepStatus.pending
                if level.level == 1
                else TicketStepStatus.not_started
            )
            step = TicketStepModel(
                id=_new_step_id(),
                tenant_id=tenant_id,
                workflow_request_id=workflow_request_id,
                ticket_flow_id=flow.id,
                level=level.level,
                status=status,
                approver_user_ids_json=json.dumps(level.approver_user_ids, sort_keys=True),
            )
            self._session.add(step)
            steps.append(step)
        await self._session.flush()
        return steps

    async def list_steps_for_request(
        self, *, workflow_request_id: str, tenant_id: str
    ) -> list[TicketStepModel]:
        result = await self._session.execute(
            select(TicketStepModel)
            .where(
                TicketStepModel.workflow_request_id == workflow_request_id,
                TicketStepModel.tenant_id == tenant_id,
            )
            .order_by(TicketStepModel.level.asc())
        )
        return list(result.scalars().all())

    async def _has_in_progress_tickets(self, *, flow_id: str, tenant_id: str) -> bool:
        result = await self._session.execute(
            select(WorkflowRequestModel.id).where(
                WorkflowRequestModel.tenant_id == tenant_id,
                WorkflowRequestModel.ticket_flow_id == flow_id,
                WorkflowRequestModel.status == WorkflowRequestStatus.pending,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _disable_enabled_flows(
        self, *, tenant_id: str, flow_type: str, except_id: str | None = None
    ) -> None:
        result = await self._session.execute(
            select(TicketFlowModel).where(
                TicketFlowModel.tenant_id == tenant_id,
                TicketFlowModel.flow_type == flow_type,
                TicketFlowModel.enabled.is_(True),
            )
        )
        for flow in result.scalars().all():
            if except_id and flow.id == except_id:
                continue
            flow.enabled = False

    async def _disable_enabled_asset_grant_flows(
        self, *, tenant_id: str, except_id: str | None = None
    ) -> None:
        await self._disable_enabled_flows(
            tenant_id=tenant_id,
            flow_type=TicketFlowType.asset_grant,
            except_id=except_id,
        )

    async def _replace_rules(
        self, *, tenant_id: str, flow_id: str, levels: list[list[str]]
    ) -> None:
        existing = await self._session.execute(
            select(ApprovalRuleModel).where(
                ApprovalRuleModel.ticket_flow_id == flow_id,
                ApprovalRuleModel.tenant_id == tenant_id,
            )
        )
        for rule in existing.scalars().all():
            await self._session.delete(rule)
        await self._session.flush()
        for index, user_ids in enumerate(levels, start=1):
            cleaned = [str(uid).strip() for uid in user_ids if str(uid).strip()]
            if not cleaned:
                raise ValueError("APPROVAL_LEVEL_REQUIRES_USERS")
            self._session.add(
                ApprovalRuleModel(
                    id=_new_rule_id(),
                    tenant_id=tenant_id,
                    ticket_flow_id=flow_id,
                    level=index,
                    approver_user_ids_json=json.dumps(cleaned, sort_keys=True),
                )
            )

    async def _flow_model(self, flow_id: str, *, tenant_id: str) -> TicketFlowModel | None:
        result = await self._session.execute(
            select(TicketFlowModel).where(
                TicketFlowModel.id == flow_id,
                TicketFlowModel.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def _snapshot(self, flow: TicketFlowModel) -> TicketFlowSnapshot:
        result = await self._session.execute(
            select(ApprovalRuleModel)
            .where(
                ApprovalRuleModel.ticket_flow_id == flow.id,
                ApprovalRuleModel.tenant_id == flow.tenant_id,
            )
            .order_by(ApprovalRuleModel.level.asc())
        )
        levels = [
            ApprovalLevelSnapshot(
                level=rule.level,
                approver_user_ids=_json_list(rule.approver_user_ids_json),
            )
            for rule in result.scalars().all()
        ]
        return TicketFlowSnapshot(
            id=flow.id,
            tenant_id=flow.tenant_id,
            name=flow.name,
            flow_type=str(getattr(flow.flow_type, "value", flow.flow_type)),
            enabled=bool(flow.enabled),
            levels=levels,
        )

    def _validate_levels(self, levels: list[list[str]]) -> None:
        if not levels or len(levels) > 3:
            raise ValueError("TICKET_FLOW_LEVELS_INVALID")
        for user_ids in levels:
            cleaned = [str(uid).strip() for uid in user_ids if str(uid).strip()]
            if not cleaned:
                raise ValueError("APPROVAL_LEVEL_REQUIRES_USERS")
            if len(cleaned) > 3:
                raise ValueError("APPROVAL_LEVEL_TOO_MANY_USERS")


def step_to_dict(step: TicketStepModel) -> dict[str, Any]:
    return {
        "id": step.id,
        "level": step.level,
        "status": str(getattr(step.status, "value", step.status)),
        "approver_user_ids": _json_list(step.approver_user_ids_json),
        "decided_by_id": step.decided_by_id or "",
        "decided_by_username": step.decided_by_username or "",
        "decided_at": step.decided_at,
        "decision_reason": step.decision_reason or "",
    }
