"""#t68 K8s 集群纳管模型（Cloud 资产 + k8s 协议）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class K8sClusterModel(Base):
    """Cloud 资产上的 Kubernetes 集群连接与 namespace 作用域配置。"""

    __tablename__ = "k8s_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id"), nullable=False, unique=True, index=True
    )
    api_server: Mapped[str] = mapped_column(String(512), nullable=False)
    server_ca_pem: Mapped[str] = mapped_column(Text, nullable=False, default="")
    namespaces_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
