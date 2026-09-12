"""#t78 分类审计：七类日志全部经 #t61 hash chain 追加，metadata 走统一脱敏。

JumpServer 对标类型在此映射为 ``event_type`` 前缀，而不是另建可篡改的平行表：
操作 / 活动 / 文件传输 / 改密 / 在线会话 / 作业 / 集成应用。
"""

from __future__ import annotations

from typing import Any

from app.api.audits.schemas import (
    AuditCategory,
    AuditEvent,
    AuditEventCreate,
    AuditKind,
    AuditSeverity,
    FileTransferLogCreate,
    JobLogCreate,
    TypedAuditCreate,
)
from app.api.audits.service import AuditService, audit_service, redact_metadata
from app.connectors.ssh_sftp import FileTransferEvent, FileTransferStatus

KIND_CATEGORY: dict[AuditKind, AuditCategory] = {
    AuditKind.operate: AuditCategory.audit,
    AuditKind.activity: AuditCategory.audit,
    AuditKind.file_transfer: AuditCategory.session,
    AuditKind.password_change: AuditCategory.auth,
    AuditKind.user_session: AuditCategory.session,
    AuditKind.job: AuditCategory.connector,
    AuditKind.integration: AuditCategory.connector,
}

KIND_SEVERITY: dict[AuditKind, AuditSeverity] = {
    AuditKind.operate: AuditSeverity.low,
    AuditKind.activity: AuditSeverity.low,
    AuditKind.file_transfer: AuditSeverity.medium,
    AuditKind.password_change: AuditSeverity.high,
    AuditKind.user_session: AuditSeverity.low,
    AuditKind.job: AuditSeverity.medium,
    AuditKind.integration: AuditSeverity.low,
}


def _actor_with_tenant(actor: dict[str, Any]) -> dict[str, Any]:
    """生产 JWT 始终带 tenant_id；测试桩缺失时回落到 default。"""

    return {**actor, "tenant_id": actor.get("tenant_id") or "default"}


def typed_event_type(kind: AuditKind, suffix: str) -> str:
    """把分类与动作拼成稳定的 ``event_type``，供 hash chain 检索。"""

    clean = suffix.strip().strip(".")
    if not clean:
        raise ValueError("TYPED_AUDIT_SUFFIX_REQUIRED")
    return f"{kind.value}.{clean}"


def kind_from_event_type(event_type: str) -> AuditKind | None:
    """从 ``event_type`` 前缀还原分类；无法识别时返回 ``None``。"""

    prefix = event_type.split(".", 1)[0]
    try:
        return AuditKind(prefix)
    except ValueError:
        return None


async def create_typed_event(
    payload: TypedAuditCreate,
    actor: dict[str, Any],
    *,
    service: AuditService = audit_service,
) -> AuditEvent:
    """写入一条分类审计。``log_kind`` 进入 metadata，因而进入 hash chain。"""

    event_type = typed_event_type(payload.kind, payload.action)
    metadata = {
        **payload.metadata,
        "log_kind": payload.kind.value,
    }
    actor_with_tenant = _actor_with_tenant(actor)
    return await service.create_event(
        AuditEventCreate(
            event_type=event_type,
            category=KIND_CATEGORY[payload.kind],
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            session_id=payload.session_id,
            severity=payload.severity or KIND_SEVERITY[payload.kind],
            message=payload.message,
            metadata=metadata,
        ),
        actor_with_tenant,
    )


async def list_typed_events(
    *,
    tenant_id: str,
    kind: AuditKind,
    limit: int,
    offset: int,
    service: AuditService = audit_service,
) -> tuple[list[AuditEvent], int]:
    """按 ``event_type`` 前缀列出某类日志。"""

    return await service.list_events(
        tenant_id=tenant_id,
        event_type=None,
        event_type_prefix=f"{kind.value}.",
        severity=None,
        limit=limit,
        offset=offset,
    )


async def record_file_transfer(
    payload: FileTransferLogCreate,
    actor: dict[str, Any],
    *,
    service: AuditService = audit_service,
) -> AuditEvent:
    """SFTP / FTP 传输日志入库。失败传输同样落链，sha256 允许为空。"""

    metadata = {
        "log_kind": AuditKind.file_transfer.value,
        "remote_path": payload.remote_path,
        "direction": payload.direction,
        "size_bytes": payload.size_bytes,
        "sha256": payload.sha256,
        "status": payload.status,
        "error_code": payload.error_code,
    }
    return await service.create_event(
        AuditEventCreate(
            event_type=typed_event_type(AuditKind.file_transfer, payload.direction),
            category=AuditCategory.session,
            action=payload.direction,
            resource_type="file",
            resource_id=payload.remote_path[:120],
            session_id=payload.session_id,
            severity=AuditSeverity.medium
            if payload.status == FileTransferStatus.SUCCESS.value
            else AuditSeverity.high,
            message=payload.error_code or payload.status,
            metadata=metadata,
        ),
        _actor_with_tenant(actor),
    )


async def record_job_log(
    payload: JobLogCreate,
    actor: dict[str, Any],
    *,
    service: AuditService = audit_service,
) -> AuditEvent:
    """作业执行日志入库。载荷已由调用方保证不含敏感键。"""

    metadata = redact_metadata(
        {
            "log_kind": AuditKind.job.value,
            "job_type": payload.job_type,
            "message_id": payload.message_id,
            "status": payload.status,
            "reason": payload.reason,
        }
    )
    return await service.create_event(
        AuditEventCreate(
            event_type=typed_event_type(AuditKind.job, payload.status),
            category=AuditCategory.connector,
            action=payload.status,
            resource_type="automation_job",
            resource_id=payload.message_id[:120],
            severity=AuditSeverity.low if payload.status == "succeeded" else AuditSeverity.high,
            message=payload.reason or payload.status,
            metadata=dict(metadata) if isinstance(metadata, dict) else {"log_kind": AuditKind.job.value},
        ),
        _actor_with_tenant(actor),
    )


async def record_password_change(actor: dict[str, Any], *, service: AuditService = audit_service) -> AuditEvent:
    """改密日志：只记录主体，不写入新旧密码。"""

    return await create_typed_event(
        TypedAuditCreate(
            kind=AuditKind.password_change,
            action="updated",
            resource_type="user",
            resource_id=str(actor["id"]),
            message="password changed",
        ),
        actor,
        service=service,
    )


class HashChainFileTransferSink:
    """把 :class:`FileTransferEvent` 写入 hash chain 的生产 sink。

    在 :meth:`bind` 时绑定会话身份；未绑定就 ``emit`` 视为配置错误。
    """

    def __init__(self, *, service: AuditService = audit_service) -> None:
        self._service = service
        self._tenant_id = ""
        self._actor: dict[str, Any] = {}
        self._session_id = ""

    def bind(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        actor_username: str,
        session_id: str,
    ) -> HashChainFileTransferSink:
        bound = HashChainFileTransferSink(service=self._service)
        bound._tenant_id = tenant_id
        bound._actor = {
            "id": actor_id,
            "username": actor_username,
            "tenant_id": tenant_id,
        }
        bound._session_id = session_id
        return bound

    async def emit(self, event: FileTransferEvent) -> None:
        """接收一条传输事件并追加到租户 hash chain。"""

        if not self._tenant_id or not self._session_id:
            raise ValueError("FILE_TRANSFER_SINK_NOT_BOUND")
        await record_file_transfer(
            FileTransferLogCreate(
                session_id=self._session_id,
                remote_path=event.remote_path,
                direction=event.direction.value,
                size_bytes=event.size_bytes,
                sha256=event.sha256,
                status=event.status.value,
                error_code=event.error_code,
            ),
            self._actor,
            service=self._service,
        )
