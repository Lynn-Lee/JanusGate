"""
资产模型：实例、平台、协议定义。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Platform(Base):
    """连接平台定义（如 Linux/SSH、Windows/RDP、MySQL 等）。"""
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="host")
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, default="host")
    protocols: Mapped[str] = mapped_column(Text, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Asset(Base):
    """资产/主机实例。"""
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, default="host", index=True)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    zone_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    platform_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trusted_ssh_ca_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(100), default="")
    credential: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
