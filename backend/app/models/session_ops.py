"""#t78 会话高级能力：共享联机、连接端点路由、命令/录像存储后端登记。

这些表不保存凭据。共享码只存 SHA-256；存储配置拒绝 password/token/secret/access_key。
分类审计仍写入 #t61 ``audit_events`` hash chain，本模块只承载可变的运营配置与联机状态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SessionShare(Base):
    """会话只读共享邀请。``code_hash`` 为分享码 SHA-256，明文码只在创建响应返回一次。"""

    __tablename__ = "session_shares"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SessionJoinRecord(Base):
    """监控联机记录：加入共享会话的观察者，不获得凭据、不可写入通道。"""

    __tablename__ = "session_join_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    share_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    joiner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    joiner_username: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="observe")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SessionEndpoint(Base):
    """连接端点：网关对外暴露的接入地址，不含凭据。"""

    __tablename__ = "session_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_session_endpoints_tenant_name"),)


class SessionEndpointRule(Base):
    """端点路由规则：按协议与可选资产匹配，优先级数字越小越先命中。"""

    __tablename__ = "session_endpoint_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    match_protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    match_asset_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    endpoint_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SessionStorageBackend(Base):
    """命令存储 / 录像存储后端登记。配置 JSON 禁止密钥类字段；云 SDK 直连属 #t70。"""

    __tablename__ = "session_storage_backends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "name", name="uq_session_storage_tenant_kind_name"),
    )
