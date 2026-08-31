"""#t65 命令过滤 ACL 与数据脱敏规则的管理 API Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.acl import (
    CommandFilterAction,
    CommandGroupMatchType,
    DataMaskingMatchType,
    DataMaskingMethod,
)


class CommandGroupPayload(BaseModel):
    """命令组写入契约：一组字面命令或正则，随 ACL 一并持久化。"""

    name: str = Field(min_length=1, max_length=128)
    match_type: CommandGroupMatchType = CommandGroupMatchType.COMMAND
    patterns: list[str] = Field(min_length=1)


class CommandGroupResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    match_type: CommandGroupMatchType
    patterns: list[str]
    is_active: bool


class CommandFilterAclCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    priority: int = Field(default=50, ge=1, le=100)
    action: CommandFilterAction = CommandFilterAction.REJECT
    reviewer_subject_ids: list[str] = Field(default_factory=list)
    subject_ids: list[str] = Field(default_factory=lambda: ["*"])
    asset_ids: list[str] = Field(default_factory=lambda: ["*"])
    account_ids: list[str] = Field(default_factory=lambda: ["*"])
    command_groups: list[CommandGroupPayload] = Field(default_factory=list)
    is_active: bool = True


class CommandFilterAclUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int | None = Field(default=None, ge=1, le=100)
    action: CommandFilterAction | None = None
    reviewer_subject_ids: list[str] | None = None
    subject_ids: list[str] | None = None
    asset_ids: list[str] | None = None
    account_ids: list[str] | None = None
    command_groups: list[CommandGroupPayload] | None = None
    is_active: bool | None = None


class CommandFilterAclResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    priority: int
    action: CommandFilterAction
    reviewer_subject_ids: list[str]
    subject_ids: list[str]
    asset_ids: list[str]
    account_ids: list[str]
    command_group_ids: list[str]
    command_groups: list[CommandGroupResponse]
    is_active: bool
    created_at: datetime | None
    updated_at: datetime | None


class CommandFilterAclListResponse(BaseModel):
    items: list[CommandFilterAclResponse]
    total: int


class DataMaskingRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    priority: int = Field(default=50, ge=1, le=100)
    match_type: DataMaskingMatchType = DataMaskingMatchType.REGEX
    patterns: list[str] = Field(min_length=1)
    mask_method: DataMaskingMethod = DataMaskingMethod.FULL
    keep_prefix: int = Field(default=0, ge=0)
    keep_suffix: int = Field(default=0, ge=0)
    placeholder: str = Field(default="***", min_length=1, max_length=32)
    subject_ids: list[str] = Field(default_factory=lambda: ["*"])
    asset_ids: list[str] = Field(default_factory=lambda: ["*"])
    account_ids: list[str] = Field(default_factory=lambda: ["*"])
    is_active: bool = True


class DataMaskingRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int | None = Field(default=None, ge=1, le=100)
    match_type: DataMaskingMatchType | None = None
    patterns: list[str] | None = Field(default=None, min_length=1)
    mask_method: DataMaskingMethod | None = None
    keep_prefix: int | None = Field(default=None, ge=0)
    keep_suffix: int | None = Field(default=None, ge=0)
    placeholder: str | None = Field(default=None, min_length=1, max_length=32)
    subject_ids: list[str] | None = None
    asset_ids: list[str] | None = None
    account_ids: list[str] | None = None
    is_active: bool | None = None


class DataMaskingRuleResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    priority: int
    match_type: DataMaskingMatchType
    patterns: list[str]
    mask_method: DataMaskingMethod
    keep_prefix: int
    keep_suffix: int
    placeholder: str
    subject_ids: list[str]
    asset_ids: list[str]
    account_ids: list[str]
    is_active: bool
    created_at: datetime | None
    updated_at: datetime | None


class DataMaskingRuleListResponse(BaseModel):
    items: list[DataMaskingRuleResponse]
    total: int
