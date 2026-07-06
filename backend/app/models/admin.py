"""Admin persistence models."""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LicenseConfigurationModel(Base):
    __tablename__ = "license_configurations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    configured_edition: Mapped[str] = mapped_column(String(32), nullable=False)
    license_verifier: Mapped[str] = mapped_column(String(32), nullable=False)
    license_key: Mapped[str] = mapped_column(Text, nullable=False)
    license_signing_secret: Mapped[str] = mapped_column(Text, nullable=False, default="")
    license_public_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
