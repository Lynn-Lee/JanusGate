"""#t77 作业中心 API schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JobPlaybookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    filename: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=65_536)


class JobPlaybookResponse(BaseModel):
    id: int
    name: str
    filename: str
    content: str


class JobPlaybookListResponse(BaseModel):
    items: list[JobPlaybookResponse]
    total: int


class JobVariableCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    extra_vars: dict[str, Any] = Field(default_factory=dict)


class JobVariableResponse(BaseModel):
    id: int
    name: str
    extra_vars: dict[str, Any]


class JobVariableListResponse(BaseModel):
    items: list[JobVariableResponse]
    total: int


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: Literal["playbook", "adhoc"]
    playbook_id: int | None = Field(default=None, gt=0)
    adhoc_module: str = Field(default="command", max_length=32)
    adhoc_args: str = Field(default="", max_length=1024)
    target_asset_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=200)
    extra_var_names: list[str] = Field(default_factory=list, max_length=32)
    runas_account_id: int | None = Field(default=None, gt=0)
    interval_seconds: int | None = Field(default=None, gt=0)
    enabled: bool = True
    check_mode: bool = False


class JobResponse(BaseModel):
    id: int
    name: str
    kind: str
    playbook_id: int | None
    adhoc_module: str
    adhoc_args: str
    target_asset_ids: list[int]
    extra_var_names: list[str]
    runas_account_id: int | None
    interval_seconds: int | None
    next_run_at: datetime | None
    enabled: bool
    check_mode: bool


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int


class JobExecutionResponse(BaseModel):
    id: int
    job_id: int
    message_id: str
    status: str
    requested_by: str
    error_code: str | None
    queued_at: datetime | None = None


class JobExecutionListResponse(BaseModel):
    items: list[JobExecutionResponse]
    total: int


class JobRunResponse(BaseModel):
    job_id: int
    message_id: str
    job_type: str
    status: str


class SchedulerTickResponse(BaseModel):
    queued: int
    items: list[JobRunResponse]
