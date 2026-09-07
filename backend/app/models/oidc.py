"""OIDC 认证源（租户级，至多一条配置 / 一个启用）。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OidcProvider(Base):
    """租户 OIDC 提供方。每租户一行；启用时须字段齐全。"""

    __tablename__ = "oidc_providers"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_oidc_providers_tenant_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    issuer_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="openid profile email")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
