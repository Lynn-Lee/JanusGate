"""SSH 主机密钥信任记录：采集后须审批才能固定，绝不 TOFU。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HostKeyTrustStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class HostKeyPresentation(StrEnum):
    UNKNOWN = "unknown"
    CHANGED = "changed"
    APPROVED = "approved"


class AssetHostKeyModel(Base):
    """每个租户资产一行：只有 approved 公钥可用于建连。"""

    __tablename__ = "asset_host_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "asset_id", name="uq_asset_host_keys_tenant_asset"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    approved_public_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    approved_fingerprint: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    pending_public_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pending_fingerprint: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    pending_state: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    pending_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    workflow_request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
