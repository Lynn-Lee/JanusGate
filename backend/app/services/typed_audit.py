"""#t78 分类审计类型常量与文件传输 hash chain 入库。"""
from __future__ import annotations

from typing import Any

from app.api.audits.schemas import AuditCategory, AuditEventCreate, AuditSeverity
from app.api.audits.service import AuditService, audit_service
from app.connectors.ssh_sftp import FileTransferEvent

TYPED_AUDIT_LOGS: dict[str, tuple[str, AuditCategory]] = {
    "operate": ("operate.action", AuditCategory.operate),
    "activity": ("activity.event", AuditCategory.activity),
    "ftp": ("ftp.transfer", AuditCategory.ftp),
    "password": ("password.changed", AuditCategory.password),
    "online-sessions": ("session.online", AuditCategory.session),
    "job": ("job.execution", AuditCategory.job),
    "session-shares": ("session.share", AuditCategory.session),
}


class HashChainFileTransferSink:
    """把 SFTP ``FileTransferEvent`` 写入 #t61 hash chain + WORM 审计账本。"""

    def __init__(
        self,
        *,
        actor: dict[str, Any],
        session_id: str,
        asset_id: str,
        service: AuditService | None = None,
    ) -> None:
        self._actor = actor
        self._session_id = session_id
        self._asset_id = asset_id
        self._service = service or audit_service

    async def emit(self, event: FileTransferEvent) -> None:
        """失败传输同样落库，保证审计可见。"""

        await self._service.create_event(
            AuditEventCreate(
                event_type="ftp.transfer",
                category=AuditCategory.ftp,
                action=event.direction.value,
                resource_type="file_transfer",
                resource_id=event.remote_path[:120],
                session_id=self._session_id,
                severity=AuditSeverity.medium if event.status.value == "failed" else AuditSeverity.low,
                message=f"sftp {event.direction.value} {event.status.value}",
                metadata={
                    "asset_id": self._asset_id,
                    "size_bytes": event.size_bytes,
                    "sha256": event.sha256,
                    "status": event.status.value,
                    "error_code": event.error_code,
                },
            ),
            self._actor,
        )
