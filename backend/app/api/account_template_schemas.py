"""#t73 账号模板 API schemas。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AccountTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    default_username: str = Field(min_length=1, max_length=100)


class AccountTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    default_username: str | None = Field(default=None, min_length=1, max_length=100)


class AccountTemplateResponse(BaseModel):
    id: int
    name: str
    protocol: str
    default_username: str


class AccountTemplateListResponse(BaseModel):
    items: list[AccountTemplateResponse]
    total: int
