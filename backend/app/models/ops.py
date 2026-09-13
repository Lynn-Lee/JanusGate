"""#t77 作业中心：Playbook 登记、作业定义、参数变量与执行记录。

约束：
- Playbook 只登记相对路径，不入库正文或凭据
- 变量 / extra vars 拒绝敏感键与 Jinja
- 执行身份是租户内 SSH 账号（runas），凭据不进队列
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OpsPlaybook(Base):
    """租户内 Playbook 目录项。只登记相对 ``.yml/.yaml`` 路径。"""

    __tablename__ = "ops_playbooks"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ops_playbooks_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpsJob(Base):
    """可重复执行的作业。``playbook`` 绑定目录项；``adhoc`` 走内置临时命令 playbook。"""

    __tablename__ = "ops_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ops_jobs_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    playbook_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ops_playbooks.id", ondelete="SET NULL", name="fk_ops_jobs_playbook_id_ops_playbooks"),
        nullable=True,
        index=True,
    )
    adhoc_module: Mapped[str | None] = mapped_column(String(32), nullable=True)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_asset_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    extra_vars_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    runas_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="RESTRICT", name="fk_ops_jobs_runas_account_id_accounts"),
        nullable=False,
        index=True,
    )
    cron_expr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    check_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpsJobVariable(Base):
    """作业参数。默认值入库前必须经过 sanitizer，不得保存凭据。"""

    __tablename__ = "ops_job_variables"
    __table_args__ = (
        UniqueConstraint("job_id", "name", name="uq_ops_job_variables_job_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ops_jobs.id", ondelete="CASCADE", name="fk_ops_job_variables_job_id_ops_jobs"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    default_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class OpsJobExecution(Base):
    """一次作业执行。队列 payload 只引用本表 id，命令与变量从这里加载。"""

    __tablename__ = "ops_job_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ops_jobs.id", ondelete="SET NULL", name="fk_ops_job_executions_job_id_ops_jobs"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    playbook_name: Mapped[str] = mapped_column(String(255), nullable=False)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    adhoc_module: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_asset_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    extra_vars_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    runas_account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    check_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
