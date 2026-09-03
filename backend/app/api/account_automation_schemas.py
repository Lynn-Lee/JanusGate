"""#t73 账号模板、风险与自动化 API schemas。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AccountTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=100)
    protocol: str = Field(default="ssh", min_length=1, max_length=32)
    privileged: bool = False
    login_shell: str | None = Field(default=None, max_length=120)
    home_dir: str | None = Field(default=None, max_length=240)
    groups: list[str] = Field(default_factory=list, max_length=32)
    organization_id: str | None = Field(default=None, min_length=1, max_length=64)
    team_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: str = Field(default="active", min_length=1, max_length=20)


class AccountTemplateResponse(BaseModel):
    id: int
    tenant_id: str
    name: str
    username: str
    protocol: str
    privileged: bool
    login_shell: str | None
    home_dir: str | None
    groups: list[str]
    organization_id: str | None
    team_id: str | None
    project_id: str | None
    status: str


class AccountTemplateListResponse(BaseModel):
    items: list[AccountTemplateResponse]
    total: int


class AccountRiskResponse(BaseModel):
    id: int
    tenant_id: str
    asset_id: int | None
    account_id: int | None
    username: str
    risk_type: str
    severity: str
    detail: str | None
    status: str
    source_job_type: str | None
    created_at: datetime | None = None


class AccountRiskListResponse(BaseModel):
    items: list[AccountRiskResponse]
    total: int


class AccountRiskResolveRequest(BaseModel):
    status: str = Field(default="resolved", pattern="^(resolved|open|accepted)$")


class AccountAutomationRunResponse(BaseModel):
    id: int
    message_id: str
    job_type: str
    status: str
    requested_by: str
    account_id: int | None
    asset_id: int | None
    template_id: int | None
    result_summary: str | None
    error_code: str | None


class AccountAutomationRunListResponse(BaseModel):
    items: list[AccountAutomationRunResponse]
    total: int


class AccountJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=240)


class AccountGatherJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)


class AccountPushJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(gt=0)
    template_id: int = Field(gt=0)
