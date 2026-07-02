"""Phase 4 tenancy API schemas."""
from pydantic import BaseModel, Field


class OrganizationCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    status: str = Field(default="active", max_length=20)


class OrganizationResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    status: str


class OrganizationListResponse(BaseModel):
    items: list[OrganizationResponse]
    total: int


class TeamCreate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    organization_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class TeamResponse(BaseModel):
    id: str
    tenant_id: str
    organization_id: str
    name: str


class TeamListResponse(BaseModel):
    items: list[TeamResponse]
    total: int
