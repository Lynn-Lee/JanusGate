"""#t77 作业中心 API schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlaybookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=240)


class PlaybookResponse(BaseModel):
    id: int
    name: str
    filename: str
    description: str
    is_active: bool


class PlaybookListResponse(BaseModel):
    items: list[PlaybookResponse]
    total: int


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    job_kind: str = Field(min_length=1, max_length=16)
    playbook_id: int | None = Field(default=None, gt=0)
    adhoc_module: str | None = Field(default=None, max_length=32)
    adhoc_command: str | None = Field(default=None, max_length=4000)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    target_asset_ids: list[int] = Field(min_length=1, max_length=200)
    runas_account_id: int = Field(gt=0)
    cron_expr: str | None = Field(default=None, max_length=64)
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True


class JobResponse(BaseModel):
    id: int
    name: str
    job_kind: str
    playbook_id: int | None
    adhoc_module: str | None
    adhoc_command: str | None
    extra_vars: dict[str, Any]
    extra_var_keys: list[str]
    target_asset_ids: list[int]
    runas_account_id: int
    cron_expr: str | None
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    created_by: str


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int


class JobVariableCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=2000)


class JobVariableResponse(BaseModel):
    id: int
    job_id: int
    name: str
    value: str


class JobVariableListResponse(BaseModel):
    items: list[JobVariableResponse]
    total: int


class JobRunResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    ops_job_id: int


class JobExecutionResponse(BaseModel):
    message_id: str
    job_type: str
    status: str
    requested_by: str
    ops_job_id: int | None
    job_kind: str | None
    playbook_name: str | None
    runas_account_id: int | None
    extra_var_keys: list[str]
    target_count: int | None
    error_code: str | None


class JobExecutionListResponse(BaseModel):
    items: list[JobExecutionResponse]
    total: int


class SchedulerTickResponse(BaseModel):
    enqueued: int
    skipped: int
