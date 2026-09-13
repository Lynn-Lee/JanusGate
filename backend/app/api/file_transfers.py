"""连接器文件传输审计入库（#t78 FTPLog）。

``POST /api/v1/connectors/{connector_id}/file-transfers`` 是 #t69 SFTP
:class:`~app.connectors.ssh_sftp.FileTransferEventSink` 的 HTTP 落点。事件经
:func:`record_file_transfer` 并入 #t61 hash chain，不另建可绕过账本的表。

跨租户 / 非 active 连接器一律 404，避免探测；响应与错误不含文件正文、token 或凭据。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audits.schemas import AuditEvent, FileTransferIngest
from app.api.audits.typed import record_file_transfer
from app.core.database import get_db
from app.core.deps import current_user
from app.models.connector import Connector

router = APIRouter(tags=["文件传输审计"])


@router.post(
    "/connectors/{connector_id}/file-transfers",
    response_model=AuditEvent,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_connector_file_transfer(
    connector_id: int,
    data: FileTransferIngest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> AuditEvent:
    _require_ingest_permission(user)
    await _get_active_scoped_connector(db=db, user=user, connector_id=connector_id)
    return await record_file_transfer(
        ingest=data,
        connector_id=str(connector_id),
        actor=user,
    )


async def _get_active_scoped_connector(
    *, db: AsyncSession, user: dict[str, Any], connector_id: int
) -> Connector:
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(Connector).where(Connector.id == connector_id).where(Connector.tenant_id == tenant_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None or connector.status != "active":
        raise HTTPException(status_code=404, detail="CONNECTOR_NOT_FOUND")
    return connector


def _require_ingest_permission(user: dict[str, Any]) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or "connectors:write" in permissions:
        return
    raise HTTPException(status_code=403, detail="缺少权限: connectors:write")
