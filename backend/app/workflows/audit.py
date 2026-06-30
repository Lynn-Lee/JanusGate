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
}


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
