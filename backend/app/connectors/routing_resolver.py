"""按协议路由到 SSH 或 K8s 连接解析器。"""

from __future__ import annotations

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.session_runtime import SessionConnectionResolver, SessionConnectionSpec


class RoutingSessionConnectionResolver:
    """生产装配：``k8s`` 协议走 K8s resolver，其余 SSH 家族走 AssetVault resolver。"""

    def __init__(
        self,
        *,
        ssh_resolver: SessionConnectionResolver,
        k8s_resolver: SessionConnectionResolver,
    ) -> None:
        self._ssh = ssh_resolver
        self._k8s = k8s_resolver

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        if request.protocol.lower() == "k8s":
            return await self._k8s.resolve(request)
        return await self._ssh.resolve(request)
