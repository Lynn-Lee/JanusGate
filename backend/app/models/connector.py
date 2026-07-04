"""Phase 4 persistent connector records."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_connectors_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(String(160), nullable=False)
    previous_public_key_fingerprint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mtls_certificate_fingerprint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attestation_nonce: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attestation_digest: Mapped[str | None] = mapped_column(String(160), nullable=True)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    key_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
