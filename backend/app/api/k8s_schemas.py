"""#t68 K8s 集群管理 API schemas。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class K8sClusterUpsert(BaseModel):
    api_server: str = Field(min_length=8, max_length=512)
    server_ca_pem: str = Field(min_length=1)
    namespaces: list[str] = Field(default_factory=list)


class K8sClusterResponse(BaseModel):
    asset_id: int
    tenant_id: str
    api_server: str
    namespaces: list[str]
    has_server_ca: bool = True
