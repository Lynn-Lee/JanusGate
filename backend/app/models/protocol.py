"""#t66 协议定义与 Platform 协议约束模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProtocolModel(Base):
    """全局协议声明，不按租户隔离。"""

    __tablename__ = "protocols"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    default_port: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    credential_types_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    driver_module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PlatformProtocolModel(Base):
    """Platform 允许的协议及端口/设置约束。"""

    __tablename__ = "platform_protocols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_id: Mapped[int] = mapped_column(Integer, ForeignKey("platforms.id"), index=True)
    protocol_id: Mapped[str] = mapped_column(String(32), ForeignKey("protocols.id"), index=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
