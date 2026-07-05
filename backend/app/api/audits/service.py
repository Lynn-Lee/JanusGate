"""审计事件存储与 SIEM 投递服务首版。"""
import hashlib
import hmac
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.api.audits.schemas import (
    AuditComplianceReport,
    AuditEvent,
    AuditEventCreate,
    AuditReportSummary,
    AuditSeverity,
    SiemDeliveryStatus,
)
from app.core.config import settings

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "private_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "ssh_key",
}


@dataclass(frozen=True)
class ComplianceReportArchiveRecord:
    id: str
    tenant_id: str
    template: str
    sequence_number: int
    content_hash: str
    report_signature: str
    created_at: datetime


class AuditEventRepository:
    """进程内 append-only 审计事件仓库。

    当前仓库用于第一版 API 和测试闭环；后续可替换为 SQLAlchemy/WORM 实现，路由层不变。
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._compliance_report_archive: list[ComplianceReportArchiveRecord] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        self._events.append(event)
        return event

    def next_sequence(self) -> int:
        return len(self._events) + 1

    def latest_hash(self) -> str | None:
        if not self._events:
            return None
        return self._events[-1].event_hash

    def list(
        self,
        *,
        tenant_id: str,
        event_type: str | None = None,
        severity: AuditSeverity | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        matches = [
            event
            for event in self._events
            if event.tenant_id == tenant_id
            and (event_type is None or event.event_type == event_type)
            and (severity is None or event.severity == severity)
        ]
        return matches[offset : offset + limit], len(matches)

    def summary(self, *, tenant_id: str) -> AuditReportSummary:
        matches = [event for event in self._events if event.tenant_id == tenant_id]
        severity_counts = Counter(event.severity.value for event in matches)
        category_counts = Counter(event.category.value for event in matches)
        siem_counts = Counter(event.siem_delivery_status.value for event in matches)
        return AuditReportSummary(
            tenant_id=tenant_id,
            total=len(matches),
            high_or_critical_total=sum(
                severity_counts[severity.value]
                for severity in (AuditSeverity.high, AuditSeverity.critical)
            ),
            by_severity=dict(severity_counts),
            by_category=dict(category_counts),
            by_siem_delivery_status=dict(siem_counts),
        )

    def compliance_report(self, *, tenant_id: str, template: str) -> AuditComplianceReport:
        matches = [event for event in self._events if event.tenant_id == tenant_id]
        generated_at = datetime.now(UTC)
        report = AuditComplianceReport(
            download_filename=build_compliance_report_filename(
                tenant_id=tenant_id,
                template=template,
                generated_at=generated_at,
            ),
            tenant_id=tenant_id,
            template=template,
            total=len(matches),
            event_ids=[event.id for event in matches],
            hash_chain_start=matches[0].event_hash if matches else None,
            hash_chain_end=matches[-1].event_hash if matches else None,
            period_start=matches[0].created_at if matches else None,
            period_end=matches[-1].created_at if matches else None,
            generated_at=generated_at,
            report_signature="",
            worm_storage_status="pending",
            worm_record_id="",
            worm_sequence_number=0,
            worm_content_hash="",
        )
        report.report_signature = sign_compliance_report(report)
        archive_record = self._append_compliance_report_archive(report)
        report.worm_storage_status = "recorded"
        report.worm_record_id = archive_record.id
        report.worm_sequence_number = archive_record.sequence_number
        report.worm_content_hash = archive_record.content_hash
        return report

    def _append_compliance_report_archive(
        self, report: AuditComplianceReport
    ) -> ComplianceReportArchiveRecord:
        archive_record = ComplianceReportArchiveRecord(
            id=str(uuid4()),
            tenant_id=report.tenant_id,
            template=report.template,
            sequence_number=len(self._compliance_report_archive) + 1,
            content_hash=calculate_compliance_report_content_hash(report),
            report_signature=report.report_signature,
            created_at=datetime.now(UTC),
        )
        self._compliance_report_archive.append(archive_record)
        return archive_record

    def clear(self) -> None:
        self._events.clear()
        self._compliance_report_archive.clear()


class SiemDeliveryError(RuntimeError):
    """SIEM 投递失败。"""


class SiemDeliveryClient:
    """SIEM/Webhook 投递接口首版。

    暂以可替换的 Python 接口表达投递边界；真实 HTTP/syslog/Kafka 投递由后续配置接入。
    """

    async def deliver(self, event: AuditEvent) -> None:
        if event.metadata.get("force_siem_failure") is True:
            raise SiemDeliveryError("SIEM delivery rejected by test sink")


class AuditService:
    def __init__(self, repository: AuditEventRepository, siem_client: SiemDeliveryClient) -> None:
        self._repository = repository
        self._siem_client = siem_client

    async def create_event(self, payload: AuditEventCreate, actor: dict[str, Any]) -> AuditEvent:
        sequence_number = self._repository.next_sequence()
        previous_hash = self._repository.latest_hash()
        event = AuditEvent(
            tenant_id=str(actor["tenant_id"]),
            actor_id=str(actor["id"]),
            actor_username=str(actor.get("username", "")),
            event_type=payload.event_type,
            category=payload.category,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            session_id=payload.session_id,
            severity=payload.severity,
            message=payload.message,
            metadata=redact_metadata(payload.metadata),
            sequence_number=sequence_number,
            previous_event_hash=previous_hash,
            event_hash="",
        )
        event.event_hash = calculate_event_hash(event)
        self._repository.append(event)
        try:
            event.siem_delivery_attempts += 1
            await self._siem_client.deliver(event)
            event.siem_delivery_status = SiemDeliveryStatus.delivered
        except SiemDeliveryError as exc:
            event.siem_delivery_status = SiemDeliveryStatus.failed
            event.siem_delivery_error = str(exc)
            event.siem_next_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        return event

    def list_events(
        self,
        *,
        tenant_id: str,
        event_type: str | None,
        severity: AuditSeverity | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditEvent], int]:
        return self._repository.list(
            tenant_id=tenant_id,
            event_type=event_type,
            severity=severity,
            limit=limit,
            offset=offset,
        )

    def report_summary(self, *, tenant_id: str) -> AuditReportSummary:
        return self._repository.summary(tenant_id=tenant_id)

    def compliance_report(self, *, tenant_id: str, template: str) -> AuditComplianceReport:
        return self._repository.compliance_report(tenant_id=tenant_id, template=template)


def calculate_event_hash(event: AuditEvent) -> str:
    canonical = event.model_dump(
        mode="json",
        exclude={
            "event_hash",
            "siem_delivery_status",
            "siem_delivery_error",
            "siem_delivery_attempts",
            "siem_next_retry_at",
        },
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sign_compliance_report(report: AuditComplianceReport) -> str:
    canonical = report.model_dump(
        mode="json",
        exclude={
            "report_signature",
            "worm_storage_status",
            "worm_record_id",
            "worm_sequence_number",
            "worm_content_hash",
        },
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


def calculate_compliance_report_content_hash(report: AuditComplianceReport) -> str:
    canonical = report.model_dump(
        mode="json",
        exclude={
            "generated_at",
            "report_signature",
            "worm_storage_status",
            "worm_record_id",
            "worm_sequence_number",
            "worm_content_hash",
        },
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_compliance_report_filename(
    *, tenant_id: str, template: str, generated_at: datetime
) -> str:
    date_part = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        f"janusgate-{_safe_filename_token(template)}-"
        f"{_safe_filename_token(tenant_id)}-{date_part}.json"
    )


def _safe_filename_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    token = token.strip(".-_")
    return token or "unknown"


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _is_sensitive_key(key) else redact_metadata(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or any(
        part in lowered
        for part in ("password", "passwd", "secret", "token", "authorization", "cookie", "credential")
    )


repository = AuditEventRepository()
siem_client = SiemDeliveryClient()
audit_service = AuditService(repository, siem_client)
