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


class AuditKind(StrEnum):
    """#t78 对标 JumpServer 的七类审计日志。"""

    operate = "operate"
    activity = "activity"
    file_transfer = "file_transfer"
    password_change = "password_change"
    user_session = "user_session"
    job = "job"
    integration = "integration"


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
    schema_version: str = "janusgate.audit-compliance.v1"
    export_format: str = "json"
    content_type: str = "application/vnd.janusgate.audit-compliance+json;version=1"
    download_filename: str
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
    report_signature_algorithm: str
    report_signature_key_id: str
    worm_storage_status: str
    worm_record_id: str
    worm_sequence_number: int
    worm_content_hash: str


class TypedAuditCreate(BaseModel):
    kind: AuditKind
    action: str = Field(min_length=1, max_length=80)
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(min_length=1, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)
    severity: AuditSeverity | None = None
    message: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileTransferLogCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    remote_path: str = Field(min_length=1, max_length=500)
    direction: str = Field(min_length=1, max_length=16)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(default="", max_length=64)
    status: str = Field(min_length=1, max_length=16)
    error_code: str = Field(default="", max_length=80)

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"upload", "download"}:
            raise ValueError("FILE_TRANSFER_DIRECTION_INVALID")
        return cleaned

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"success", "failed"}:
            raise ValueError("FILE_TRANSFER_STATUS_INVALID")
        return cleaned

    @field_validator("sha256")
    @classmethod
    def validate_sha(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned and (len(cleaned) != 64 or any(ch not in "0123456789abcdef" for ch in cleaned)):
            raise ValueError("FILE_TRANSFER_SHA256_INVALID")
        return cleaned


class JobLogCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=80)
    message_id: str = Field(min_length=1, max_length=120)
    status: str = Field(min_length=1, max_length=32)
    reason: str = Field(default="", max_length=240)

    @field_validator("status")
    @classmethod
    def validate_job_status(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"succeeded", "failed"}:
            raise ValueError("JOB_LOG_STATUS_INVALID")
        return cleaned


class OnlineSessionItem(BaseModel):
    id: str
    subject_id: str
    asset_id: str
    account_id: str
    protocol: str
    status: str
    created_at: datetime


class OnlineSessionList(BaseModel):
    items: list[OnlineSessionItem]
    total: int
