"""Workflow/JIT audit event helpers."""
import asyncio
from typing import Any

from app.api.audits.schemas import AuditCategory, AuditEvent, AuditEventCreate
from app.api.audits.service import AuditService

WORKFLOW_AUDIT_EVENTS = {
    "workflow.request.created",
    "workflow.request.submitted",
    "workflow.request.approved",
    "workflow.request.rejected",
    "workflow.request.revoked",
    "workflow.request.expired",
    "jit.grant.issued",
    "jit.grant.used",
    "jit.grant.expired",
    "jit.grant.revoked",
    "session.revoked_by_jit_grant",
}


class WorkflowAuditSink:
    """Bridge Workflow/Session service lifecycle events into AuditService."""

    def __init__(self, audit_service: AuditService) -> None:
        self._audit_service = audit_service

    async def publish(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type not in WORKFLOW_AUDIT_EVENTS:
            return
        workflow_request_id = str(event.get("workflow_request_id") or "")
        jit_grant_id = event.get("jit_grant_id")
        resource_type, resource_id = self._resource_from_event(event_type, event)
        await emit_workflow_audit_event_async(
            self._audit_service,
            actor={
                "id": event.get("actor_id") or event.get("requester_id") or "system",
                "username": event.get("actor_username") or "system",
                "tenant_id": event.get("tenant_id", "default"),
            },
            event_type=event_type,
            workflow_request_id=workflow_request_id,
            jit_grant_id=str(jit_grant_id) if jit_grant_id else None,
            action=str(event.get("action") or event_type.rsplit(".", 1)[-1]),
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=dict(event),
        )

    def _resource_from_event(self, event_type: str, event: dict[str, Any]) -> tuple[str, str]:
        if event_type.startswith("workflow.request."):
            return "workflow_request", str(event.get("workflow_request_id") or "unknown")
        if event_type.startswith("jit.grant."):
            return "jit_grant", str(event.get("jit_grant_id") or "unknown")
        if event_type == "session.revoked_by_jit_grant":
            return "session", str(event.get("session_id") or "unknown")
        return "workflow", str(event.get("workflow_request_id") or event.get("jit_grant_id") or "unknown")


async def emit_workflow_audit_event_async(
    audit_service: AuditService,
    *,
    actor: dict[str, Any],
    event_type: str,
    workflow_request_id: str,
    jit_grant_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any],
) -> AuditEvent:
    if event_type not in WORKFLOW_AUDIT_EVENTS:
        raise ValueError(f"Unsupported Workflow/JIT audit event: {event_type}")
    enriched_metadata = {
        **metadata,
        "workflow_request_id": workflow_request_id,
        "jit_grant_id": jit_grant_id,
    }
    payload = AuditEventCreate(
        event_type=event_type,
        category=AuditCategory.workflow,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=enriched_metadata,
    )
    return await audit_service.create_event(payload, actor)


def emit_workflow_audit_event(
    audit_service: AuditService,
    *,
    actor: dict[str, Any],
    event_type: str,
    workflow_request_id: str,
    jit_grant_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any],
) -> AuditEvent:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            emit_workflow_audit_event_async(
                audit_service,
                actor=actor,
                event_type=event_type,
                workflow_request_id=workflow_request_id,
                jit_grant_id=jit_grant_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=metadata,
            )
        )
    raise RuntimeError("emit_workflow_audit_event cannot be called from a running event loop")
