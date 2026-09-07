"""Phase 4 account custody models (+ #t73 AccountTemplate / AccountRisk / verify)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class AccountTemplate(Base):
    """租户内账号模板：名称 + 固定 ssh 协议 + 默认用户名。"""

    __tablename__ = "account_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_account_templates_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="ssh")
    default_username: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    # #t68 K8s TokenRequest：默认关闭；开启后 Vault 仅提供 bootstrap，会话用短期令牌。
    use_token_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    # #t73 账号模板（删除模板仅 unlink，不级联删账号）。
    template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("account_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # #t73 校验状态：unverified / verifying / success / failed（UI：未校验/校验中/成功/失败）
    verify_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    last_verify_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_verify_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # #t73 推送状态：unpushed / pushing / success / failed
    push_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unpushed")
    last_push_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    asset: Mapped[Asset] = relationship()


class AccountRisk(Base):
    """#t73 账号风险记录（verify_failed / push_failed）。"""

    __tablename__ = "account_risks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(240), nullable=False, default="校验失败")
    job_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
