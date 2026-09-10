"""#t79 平台治理 API schemas。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(default="#2563eb", min_length=7, max_length=7)


class LabelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = Field(default=None, min_length=7, max_length=7)


class LabelResponse(BaseModel):
    id: int
    name: str
    color: str
    asset_ids: list[int]


class LabelListResponse(BaseModel):
    items: list[LabelResponse]
    total: int


class LabelBindingUpdate(BaseModel):
    asset_ids: list[int] = Field(default_factory=list)


class SettingItem(BaseModel):
    key: str
    value: bool | int | str


class SettingListResponse(BaseModel):
    items: list[SettingItem]


class SettingUpdateRequest(BaseModel):
    items: list[SettingItem]


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


class PreferenceListResponse(BaseModel):
    items: list[SettingItem]


class PreferenceUpdateRequest(BaseModel):
    items: list[SettingItem]


class LeakPasswordCreate(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=256)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


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
    template_key: str = Field(min_length=3, max_length=80)
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
    template_key: str | None = Field(default=None, min_length=3, max_length=80)
    report_id: int | None = None


class ReportRunResponse(BaseModel):
    template_key: str
    result: dict[str, Any]
