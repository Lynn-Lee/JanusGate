"""#t77 job center request/response schemas."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class JobPlaybookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    playbook_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    is_active: bool = True


class JobPlaybookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    playbook_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class JobPlaybookResponse(BaseModel):
    id: int
    name: str
    playbook_name: str
    description: str
    is_active: bool


class JobPlaybookListResponse(BaseModel):
    items: list[JobPlaybookResponse]
    total: int


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    playbook_id: int = Field(gt=0)
    target_asset_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=200)
    check_mode: bool = False
    description: str = Field(default="", max_length=2000)
    is_active: bool = True


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    playbook_id: int | None = Field(default=None, gt=0)
    target_asset_ids: list[Annotated[int, Field(gt=0)]] | None = Field(
        default=None, min_length=1, max_length=200
    )
    check_mode: bool | None = None
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class JobResponse(BaseModel):
    id: int
    name: str
    playbook_id: int
    playbook_name: str | None = None
    target_asset_ids: list[int]
    check_mode: bool
    description: str
    is_active: bool


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int


class JobRunRequest(BaseModel):
    """Optional per-run overrides (light parameterization)."""

    model_config = ConfigDict(extra="forbid")

    target_asset_ids: list[Annotated[int, Field(gt=0)]] | None = Field(
        default=None, min_length=1, max_length=200
    )
    check_mode: bool | None = None


class JobRunResponse(BaseModel):
    execution_id: int
    job_id: int
    message_id: str
    status: str


class JobExecutionResponse(BaseModel):
    id: int
    job_id: int
    message_id: str
    status: str
    requested_by: str
    playbook_name: str
    check_mode: bool
    target_count: int
    error_code: str | None


class JobExecutionListResponse(BaseModel):
    items: list[JobExecutionResponse]
    total: int
