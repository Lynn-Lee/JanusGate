"""#t77 作业中心：Playbook 目录、作业、变量与周期调度元数据。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OpsPlaybook(Base):
    """租户内允许引用的 Playbook 目录项；文件本身仍落在 playbook root，不入库正文。"""

    __tablename__ = "ops_playbooks"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ops_playbooks_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    filename: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpsJob(Base):
    """可重复执行的作业定义：playbook 或临时命令，含 runas 与可选 cron。"""

    __tablename__ = "ops_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ops_jobs_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    job_kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    playbook_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    adhoc_module: Mapped[str | None] = mapped_column(String(32), nullable=True)
    adhoc_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_vars_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    target_asset_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    runas_account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    cron_expr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpsJobVariable(Base):
    """作业级参数。值禁止敏感键名；执行时在 worker 侧合并，不进入队列 payload。"""

    __tablename__ = "ops_job_variables"
    __table_args__ = (UniqueConstraint("job_id", "name", name="uq_ops_job_variables_job_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
