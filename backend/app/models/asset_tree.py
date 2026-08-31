"""#t64 资产树与 AssetPermission 模型。

租户根只当容器：不授权、不挂资产、不能删。资产 ``node_id`` 为空表示未分组
（不是节点、不能继承节点授权、不上树）。判定由 PolicyDecisionService 消费，
查询强制走 scoped_select。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

NODE_RESOURCE = "node"
ASSET_RESOURCE = "asset"
CONNECT_ACTION = "connect"


class NodeModel(Base):
    """资产树节点。``parent_id is None`` 的是租户根。"""

    __tablename__ = "asset_nodes"
    __table_args__ = (
        Index(
            "uq_asset_nodes_one_root_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
            sqlite_where=text("parent_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ancestor_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssetPermissionModel(Base):
    """资产 / 节点授权，主体可为 user 或 user_group；到期空=长期。"""

    __tablename__ = "asset_permissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False, default=CONNECT_ACTION)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_ticket: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
