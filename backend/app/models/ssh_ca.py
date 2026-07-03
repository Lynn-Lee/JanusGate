"""Phase 4 SSH CA and temporary certificate models."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.account import Account
from app.models.asset import Asset


class SshCertificateAuthority(Base):
    __tablename__ = "ssh_certificate_authorities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_ssh_certificate_authorities_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_secret_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    validity_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SshCertificate(Base):
    __tablename__ = "ssh_certificates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "serial", name="uq_ssh_certificates_tenant_serial"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ca_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ssh_certificate_authorities.id"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("assets.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False, index=True
    )
    principal: Mapped[str] = mapped_column(String(120), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    serial: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="issued")
    certificate_body: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ca: Mapped[SshCertificateAuthority] = relationship()
    asset: Mapped[Asset] = relationship()
    account: Mapped[Account] = relationship()
