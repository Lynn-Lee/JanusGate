"""Phase 4 account custody models."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "asset_id", "username", "protocol", name="uq_accounts_asset_user_protocol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="ssh")
    secret_id: Mapped[str] = mapped_column(String(120), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    rotation_policy: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    k8s_namespaces_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    k8s_service_account: Mapped[str] = mapped_column(String(253), nullable=False, default="default")
    k8s_default_pod: Mapped[str] = mapped_column(String(253), nullable=False, default="")
    k8s_default_container: Mapped[str | None] = mapped_column(String(253), nullable=True)
    k8s_use_short_lived_token: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    k8s_token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    asset: Mapped[Asset] = relationship()


class CredentialRotation(Base):
    __tablename__ = "credential_rotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_secret_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    new_secret_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[Account] = relationship()
