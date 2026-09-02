"""#t67 网域与网关中转模型。

JumpServer 语义对标：``Zone`` 为名称 + 网关集合 + 随机选取；``Gateway`` 即普通
``Asset``（Host），通过 ``ZoneGatewayModel`` 关联到网域，无额外主机字段。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ZoneModel(Base):
    """网域：同一分段网络内资产的逻辑分组，经关联网关做 SSH ProxyJump。"""

    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ZoneGatewayModel(Base):
    """网域与网关资产的关联；网关凭据仍走 Vault，经 ``Account`` 解析。"""

    __tablename__ = "zone_gateways"
    __table_args__ = (
        UniqueConstraint("zone_id", "gateway_asset_id", name="uq_zone_gateways_zone_asset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(String(64), ForeignKey("zones.id"), nullable=False, index=True)
    gateway_asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id"), nullable=False, index=True
    )
    gateway_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    probe_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    probe_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
