"""按协议路由到 SSH、K8s 或 Database 连接解析器。"""

from __future__ import annotations

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.db_vault_resolver import DATABASE_PROTOCOLS
from app.connectors.session_runtime import SessionConnectionResolver, SessionConnectionSpec


class RoutingSessionConnectionResolver:
    """生产装配：数据库协议走 DB resolver，``k8s`` 走 K8s resolver，其余 SSH 家族走 AssetVault。"""

    def __init__(
        self,
        *,
        ssh_resolver: SessionConnectionResolver,
        k8s_resolver: SessionConnectionResolver,
        db_resolver: SessionConnectionResolver,
    ) -> None:
        self._ssh = ssh_resolver
        self._k8s = k8s_resolver
        self._db = db_resolver

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        protocol = request.protocol.lower()
        if protocol == "k8s":
            return await self._k8s.resolve(request)
        if protocol in DATABASE_PROTOCOLS:
            return await self._db.resolve(request)
        return await self._ssh.resolve(request)
