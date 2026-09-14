"""#t77 作业中心领域模型：Playbook / Variable / Job / JobExecution。

职责：在租户内管理可重复执行的作业目录，并与 #t52 `AutomationJobRun` 通过
`message_id` 关联。失败模式：跨租户不可见；凭据不得进入这些表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JobPlaybook(Base):
    """租户内 Playbook 目录；`filename` 必须是相对 `.yml`/`.yaml`，禁止绝对路径。"""

    __tablename__ = "job_playbooks"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_job_playbooks_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    filename: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobVariable(Base):
    """租户内作业变量（JSON extra vars）。禁止 password/token/secret 等敏感键。"""

    __tablename__ = "job_variables"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_job_variables_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    extra_vars: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Job(Base):
    """作业定义：playbook 或 adhoc；可选周期（interval_seconds + next_run_at）与 runas。"""

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_jobs_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    playbook_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("job_playbooks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    adhoc_module: Mapped[str] = mapped_column(String(32), nullable=False, default="command")
    adhoc_args: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    target_asset_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    extra_var_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    runas_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    check_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobExecution(Base):
    """作业入队记录，`message_id` 对应 Redis Stream 与 `AutomationJobRun`。"""

    __tablename__ = "job_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
