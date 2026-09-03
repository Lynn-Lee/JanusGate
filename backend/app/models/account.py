"""账号托管与账号自动化模型（#t43 / #t73）。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.asset import Asset


class Account(Base):
    """租户托管的资产账号；只存 Vault ``secret_id``，从不存明文。"""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "asset_id", "username", "protocol", name="uq_accounts_asset_user_protocol"
        ),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    asset: Mapped[Asset] = relationship()


class CredentialRotation(Base):
    """凭据轮换调度记录；仅记录 secret 引用与状态，响应侧不回传引用字段。"""

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


class AccountTemplate(Base):
    """账号模板：推送账号时复用的蓝图（用户名模式、特权标记、默认 shell/组）。"""

    __tablename__ = "account_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_account_templates_tenant_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="ssh")
    privileged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    login_shell: Mapped[str | None] = mapped_column(String(120), nullable=True)
    home_dir: Mapped[str | None] = mapped_column(String(240), nullable=True)
    groups_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    team_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AccountRisk(Base):
    """账号风险：发现的特权账号、弱密码、僵尸账号等治理结果。"""

    __tablename__ = "account_risks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "asset_id",
            "username",
            "risk_type",
            name="uq_account_risks_asset_user_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assets.id"), nullable=True, index=True
    )
    account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=True, index=True
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    detail: Mapped[str | None] = mapped_column(String(480), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    source_job_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AccountAutomationRun(Base):
    """账号自动化执行记录；摘要字段不得包含密码/私钥明文。"""

    __tablename__ = "account_automation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(String(480), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AccountBackup(Base):
    """账号元数据备份（不含明文）；用于 ``backup_account`` 自动化。"""

    __tablename__ = "account_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    secret_id_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
