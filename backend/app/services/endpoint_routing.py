"""#t78 连接端点路由：按协议与主机后缀选择 ConnectionEndpoint。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_ops import ConnectionEndpoint, EndpointRule


async def resolve_endpoint(
    db: AsyncSession,
    *,
    tenant_id: str,
    protocol: str,
    host: str,
) -> ConnectionEndpoint | None:
    """返回优先级最高且主机后缀匹配的启用端点；无规则时返回 None。"""

    result = await db.execute(
        select(EndpointRule, ConnectionEndpoint)
        .join(ConnectionEndpoint, ConnectionEndpoint.id == EndpointRule.endpoint_id)
        .where(
            EndpointRule.tenant_id == tenant_id,
            EndpointRule.enabled.is_(True),
            EndpointRule.match_protocol == protocol,
            ConnectionEndpoint.enabled.is_(True),
            ConnectionEndpoint.tenant_id == tenant_id,
        )
        .order_by(EndpointRule.priority.desc())
    )
    host_l = host.lower()
    match: ConnectionEndpoint | None = None
    for rule, endpoint in result.all():
        suffix = (rule.match_host_suffix or "").lower()
        if not suffix or host_l.endswith(suffix):
            match = endpoint
            break
    return match
