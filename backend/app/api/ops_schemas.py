"""#t77 作业中心 API schema。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OpsPlaybookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    relative_path: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=240)


class OpsPlaybookResponse(BaseModel):
    id: int
    name: str
    relative_path: str
    description: str
    status: str


class OpsPlaybookListResponse(BaseModel):
    items: list[OpsPlaybookResponse]
    total: int


class OpsJobVariableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    default_value: str = Field(default="", max_length=2000)
    required: bool = False


class OpsJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    job_type: str = Field(min_length=1, max_length=32)
    runas_account_id: int = Field(gt=0)
    target_asset_ids: list[int] = Field(min_length=1, max_length=200)
    playbook_id: int | None = None
    adhoc_module: str | None = None
    command: str | None = None
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    variables: list[OpsJobVariableInput] = Field(default_factory=list)
    cron_expr: str | None = None
    check_mode: bool = False


class OpsJobResponse(BaseModel):
    id: int
    name: str
    job_type: str
    playbook_id: int | None
    adhoc_module: str | None
    command: str | None
    target_asset_ids: list[int]
    extra_vars: dict[str, Any]
    runas_account_id: int
    cron_expr: str | None
    next_run_at: datetime | None
    check_mode: bool
    status: str


class OpsJobListResponse(BaseModel):
    items: list[OpsJobResponse]
    total: int


class OpsJobRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extra_vars: dict[str, Any] = Field(default_factory=dict)


class OpsAdhocCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runas_account_id: int = Field(gt=0)
    target_asset_ids: list[int] = Field(min_length=1, max_length=200)
    adhoc_module: str = Field(default="command", max_length=32)
    command: str = Field(min_length=1, max_length=2000)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    check_mode: bool = False


class OpsExecutionResponse(BaseModel):
    id: int
    job_id: int | None
    message_id: str | None
    job_type: str
    playbook_name: str
    command: str | None
    extra_vars: dict[str, Any]
    target_asset_ids: list[int]
    check_mode: bool
    status: str
    error_code: str | None
    requested_by: str
    created_at: datetime | None


class OpsExecutionListResponse(BaseModel):
    items: list[OpsExecutionResponse]
    total: int


class OpsTickResponse(BaseModel):
    queued_execution_ids: list[int]
