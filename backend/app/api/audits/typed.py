"""#t78 分类审计写入助手。

JumpServer 的 OperateLog / ActivityLog / FTPLog / PasswordChangeLog / JobLog
在 JanusGate 中**不另建可绕过账本的表**：一律经 :class:`AuditService.create_event`
并入 #t61 per-tenant hash chain 与 WORM 归档。分类只体现在 ``category`` /
``event_type``，列表 API 按类别过滤。

失败模式：调用方必须提供 ``tenant_id``（缺省 ``default``）；metadata 走既有
``redact_metadata``，不得写入明文密码、token、凭据或文件正文。
"""

from __future__ import annotations

from typing import Any

from app.api.audits.schemas import (
    AuditCategory,
    AuditEvent,
    AuditEventCreate,
    AuditSeverity,
    FileTransferIngest,
    TypedAuditLogCreate,
)
from app.api.audits.service import AuditService, audit_service

ACTIVITY_LIST_CATEGORIES = (
    AuditCategory.activity.value,
    AuditCategory.auth.value,
    AuditCategory.session.value,
)

TYPED_LIST_CATEGORIES: dict[str, tuple[str, ...]] = {
    "operate": (AuditCategory.operate.value,),
    "activity": ACTIVITY_LIST_CATEGORIES,
    "file_transfer": (AuditCategory.file_transfer.value,),
    "password_change": (AuditCategory.password_change.value,),
    "job": (AuditCategory.job.value,),
}


def actor_for_audit(actor: dict[str, Any]) -> dict[str, Any]:
    """把路由/worker 的 actor 归一成审计账本所需字段，缺 tenant 时回退 default。"""

    return {
        "id": str(actor.get("id") or "unknown"),
        "username": str(actor.get("username") or ""),
        "tenant_id": str(actor.get("tenant_id") or "default"),
        "permissions": list(actor.get("permissions") or []),
    }


async def record_typed_event(
    *,
    category: AuditCategory,
    payload: TypedAuditLogCreate,
    actor: dict[str, Any],
    service: AuditService | None = None,
) -> AuditEvent:
    """写入一条分类审计事件并计算 hash chain。"""

    writer = service or audit_service
    return await writer.create_event(
        AuditEventCreate(
            event_type=payload.event_type,
            category=category,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            session_id=payload.session_id,
            severity=payload.severity,
            message=payload.message,
            metadata=payload.metadata,
        ),
        actor_for_audit(actor),
    )


async def record_file_transfer(
    *,
    ingest: FileTransferIngest,
    connector_id: str,
    actor: dict[str, Any],
    service: AuditService | None = None,
) -> AuditEvent:
    """把一次 SFTP 传输并入 hash chain；失败传输同样落账。"""

    severity = (
        AuditSeverity.medium if ingest.status.value == "failed" else AuditSeverity.low
    )
    return await record_typed_event(
        category=AuditCategory.file_transfer,
        payload=TypedAuditLogCreate(
            event_type="session.file_transfer",
            action=f"file.{ingest.direction.value}",
            resource_type="asset",
            resource_id=ingest.asset_id,
            session_id=ingest.session_id,
            severity=severity,
            message=f"SFTP {ingest.direction.value} {ingest.status.value}",
            metadata={
                "connector_id": connector_id,
                "account_id": ingest.account_id,
                "remote_path": ingest.remote_path,
                "direction": ingest.direction.value,
                "size_bytes": ingest.size_bytes,
                "sha256": ingest.sha256,
                "status": ingest.status.value,
                "error_code": ingest.error_code,
            },
        ),
        actor=actor,
        service=service,
    )


async def record_password_change(
    *,
    actor: dict[str, Any],
    resource_type: str,
    resource_id: str,
    action: str,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    service: AuditService | None = None,
) -> AuditEvent:
    """记录登录密码修改或账号凭据轮换；metadata 不得含明文或 secret 引用。"""

    dropped = {
        "password",
        "passwd",
        "secret",
        "token",
        "secret_id",
        "old_password",
        "new_password",
        "previous_secret_id",
        "new_secret_id",
    }
    safe_metadata = {
        key: value for key, value in (metadata or {}).items() if key not in dropped
    }
    return await record_typed_event(
        category=AuditCategory.password_change,
        payload=TypedAuditLogCreate(
            event_type="auth.password_change",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            severity=AuditSeverity.medium if status != "completed" else AuditSeverity.low,
            message=message,
            metadata={"status": status, **safe_metadata},
        ),
        actor=actor,
        service=service,
    )


async def record_job_log(
    *,
    actor: dict[str, Any],
    message_id: str,
    job_type: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    service: AuditService | None = None,
) -> AuditEvent:
    """记录作业终态（completed/failed）；不保存 inventory / stdout / stderr。"""

    severity = AuditSeverity.medium if status == "failed" else AuditSeverity.low
    safe_metadata = {
        key: value
        for key, value in (metadata or {}).items()
        if key not in {"stdout", "stderr", "inventory", "output", "password", "secret", "token"}
    }
    return await record_typed_event(
        category=AuditCategory.job,
        payload=TypedAuditLogCreate(
            event_type=f"job.run.{status}",
            action=f"job.{status}",
            resource_type="automation_job",
            resource_id=message_id,
            severity=severity,
            message=f"{job_type} {status}",
            metadata={"job_type": job_type, "status": status, **safe_metadata},
        ),
        actor=actor,
        service=service,
    )
