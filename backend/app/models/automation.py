"""Phase 4 / #t77 automation job execution and job-center models."""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JobDefinition(Base):
    """作业中心可保存的作业定义（Playbook / 临时命令 / 周期任务）。

    payload 与 extra_variables 必须是 JSON 对象，禁止 pickle；敏感键由队列层拒绝。
    ``run_as_user_id`` 为空时执行身份为触发人，非空时为指定执行身份（runas）。
    """

    __tablename__ = "job_definitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    extra_variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    run_as_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AutomationJobRun(Base):
    __tablename__ = "automation_job_runs"

    message_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    playbook_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    check_mode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    job_definition_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    extra_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    run_as_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
