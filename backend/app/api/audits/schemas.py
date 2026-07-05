"""审计事件请求/响应 Schema。"""
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class AuditCategory(StrEnum):
    auth = "auth"
    policy = "policy"
    connector = "connector"
    session = "session"
    vault = "vault"
    workflow = "workflow"
    audit = "audit"


class AuditSeverity(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class SiemDeliveryStatus(StrEnum):
    pending = "pending"
    delivered = "delivered"
    failed = "failed"


class AuditEventCreate(BaseModel):
    event_type: str = Field(min_length=3, max_length=120)
    category: AuditCategory = AuditCategory.audit
    action: str = Field(min_length=1, max_length=120)
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(min_length=1, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)
    severity: AuditSeverity = AuditSeverity.low
    message: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", "action", "resource_type", "resource_id", "session_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    actor_id: str
    actor_username: str
    event_type: str
    category: AuditCategory
    action: str
    resource_type: str
    resource_id: str
    session_id: str | None = None
    severity: AuditSeverity
    message: str | None = None
    metadata: dict[str, Any]
    sequence_number: int
    previous_event_hash: str | None = None
    event_hash: str
    siem_delivery_status: SiemDeliveryStatus = SiemDeliveryStatus.pending
    siem_delivery_error: str | None = None
    siem_delivery_attempts: int = 0
    siem_next_retry_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditEventList(BaseModel):
    items: list[AuditEvent]
    total: int
    limit: int
    offset: int


class AuditReportSummary(BaseModel):
    tenant_id: str
    total: int
    high_or_critical_total: int
    by_severity: dict[str, int]
    by_category: dict[str, int]
    by_siem_delivery_status: dict[str, int]


class AuditComplianceReport(BaseModel):
    tenant_id: str
    template: str
    total: int
    event_ids: list[str]
    hash_chain_start: str | None = None
    hash_chain_end: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    report_signature: str
