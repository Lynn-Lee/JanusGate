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
    operate = "operate"
    activity = "activity"
    file_transfer = "file_transfer"
    password_change = "password_change"
    job = "job"


class FileTransferDirection(StrEnum):
    upload = "upload"
    download = "download"


class FileTransferStatus(StrEnum):
    success = "success"
    failed = "failed"


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


class TypedAuditLogCreate(BaseModel):
    """操作 / 活动等分类审计的写入请求；最终仍并入 #t61 hash chain。"""

    event_type: str = Field(min_length=3, max_length=120)
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


class FileTransferIngest(BaseModel):
    """连接器上报的一次 SFTP 文件传输（#t78 FTPLog）。"""

    session_id: str | None = Field(default=None, max_length=120)
    asset_id: str = Field(min_length=1, max_length=120)
    account_id: str = Field(min_length=1, max_length=120)
    remote_path: str = Field(min_length=1, max_length=1024)
    direction: FileTransferDirection
    size_bytes: int = Field(ge=0)
    sha256: str = Field(default="", max_length=64)
    status: FileTransferStatus
    error_code: str = Field(default="", max_length=120)

    @field_validator("session_id", "asset_id", "account_id", "remote_path", "sha256", "error_code")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        stripped = value.strip().lower()
        if not stripped:
            return ""
        if len(stripped) != 64 or any(char not in "0123456789abcdef" for char in stripped):
            raise ValueError("sha256 必须是 64 位十六进制")
        return stripped


class OnlineSession(BaseModel):
    """当前租户在线 PAM 会话（#t78 UserSession），不含连接 URL / token。"""

    id: str
    subject_id: str
    asset_id: str
    account_id: str
    protocol: str
    status: str
    client_ip: str
    created_at: datetime
    updated_at: datetime


class OnlineSessionList(BaseModel):
    items: list[OnlineSession]
    total: int


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
