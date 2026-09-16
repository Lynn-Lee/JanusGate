"""#t79 平台治理 API schemas。响应不含密钥、明文密码或审计明细。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(default="#2563eb", min_length=7, max_length=7)


class LabelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = Field(default=None, min_length=7, max_length=7)


class LabelBindingUpdate(BaseModel):
    asset_ids: list[int] = Field(default_factory=list)


class LabelResponse(BaseModel):
    id: int
    name: str
    color: str
    asset_ids: list[int]


class LabelListResponse(BaseModel):
    items: list[LabelResponse]
    total: int


class SettingItem(BaseModel):
    key: str
    value: bool | int | str


class SettingListResponse(BaseModel):
    items: list[SettingItem]


class SettingUpdateRequest(BaseModel):
    items: list[SettingItem] = Field(default_factory=list)


class PreferenceListResponse(BaseModel):
    items: list[SettingItem]


class PreferenceUpdateRequest(BaseModel):
    items: list[SettingItem] = Field(default_factory=list)


class SettingRevisionResponse(BaseModel):
    id: int
    key: str
    old_value: bool | int | str
    new_value: bool | int | str
    actor_id: str
    created_at: str


class SettingRevisionListResponse(BaseModel):
    items: list[SettingRevisionResponse]
    total: int


class LeakPasswordCreate(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    sha256: str | None = Field(default=None, max_length=64)


class LeakPasswordResponse(BaseModel):
    id: int
    sha256: str
    created_at: str


class LeakPasswordListResponse(BaseModel):
    items: list[LeakPasswordResponse]
    total: int
    builtin_count: int


class LeakPasswordCheckRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class LeakPasswordCheckResponse(BaseModel):
    leaked: bool


class ReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template_key: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)


class ReportResponse(BaseModel):
    id: int | None
    name: str
    template_key: str
    description: str
    builtin: bool


class ReportListResponse(BaseModel):
    items: list[ReportResponse]
    total: int


class ReportRunRequest(BaseModel):
    report_id: int | None = None
    template_key: str | None = Field(default=None, max_length=80)


class ReportRunResponse(BaseModel):
    template_key: str
    result: dict[str, Any]
