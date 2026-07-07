"""审计事件存储与 SIEM 投递服务首版。"""
import hashlib
import hmac
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import httpx

from app.api.audits.schemas import (
    AuditComplianceReport,
    AuditEvent,
    AuditEventCreate,
    AuditReportSummary,
    AuditSeverity,
    SiemDeliveryStatus,
)
from app.core.config import Settings, settings

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


class ComplianceReportSigner(Protocol):
    algorithm: str
    key_id: str

    def sign(self, report: AuditComplianceReport) -> str: ...


class ComplianceReportArchiveStore(Protocol):
    def append(
        self, report: AuditComplianceReport, *, content_hash: str
    ) -> ComplianceReportArchiveRecord: ...


class HmacComplianceReportSigner:
    algorithm = "hmac-sha256"
    key_id = "local-secret-key"

    def sign(self, report: AuditComplianceReport) -> str:
        return sign_compliance_report(report)


class ExternalHmacComplianceReportSigner:
    algorithm = "external-hmac-sha256"

    def __init__(self, *, key_id: str, secret: str) -> None:
        self.key_id = key_id
        self._secret = secret

    def sign(self, report: AuditComplianceReport) -> str:
        return _sign_compliance_report_with_secret(report=report, secret=self._secret)


def build_compliance_report_signer(config: Settings = settings) -> ComplianceReportSigner:
    if config.COMPLIANCE_REPORT_SIGNER_PROVIDER == "local-hmac":
        return HmacComplianceReportSigner()
    if not config.COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID.strip():
        raise ValueError(
            "COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID must be set "
            "when COMPLIANCE_REPORT_SIGNER_PROVIDER=external-hmac"
        )
    if not config.COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET.strip():
        raise ValueError(
            "COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET must be set "
            "when COMPLIANCE_REPORT_SIGNER_PROVIDER=external-hmac"
        )
    return ExternalHmacComplianceReportSigner(
        key_id=config.COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID,
        secret=config.COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET,
    )


class InMemoryComplianceReportArchiveStore:
    def __init__(self) -> None:
        self._records: list[ComplianceReportArchiveRecord] = []

    def append(
        self, report: AuditComplianceReport, *, content_hash: str
    ) -> ComplianceReportArchiveRecord:
        archive_record = ComplianceReportArchiveRecord(
            id=str(uuid4()),
            tenant_id=report.tenant_id,
            template=report.template,
            sequence_number=len(self._records) + 1,
            content_hash=content_hash,
            report_signature=report.report_signature,
            created_at=datetime.now(UTC),
        )
        self._records.append(archive_record)
        return archive_record

    def clear(self) -> None:
        self._records.clear()


class ExternalHttpComplianceReportArchiveStore:
    def __init__(self, *, url: str, token: str, timeout_seconds: float) -> None:
        if not url.startswith("https://"):
            raise ValueError("COMPLIANCE_REPORT_WORM_ARCHIVE_URL must be an HTTPS URL")
        if not token.strip():
            raise ValueError(
                "COMPLIANCE_REPORT_WORM_ARCHIVE_TOKEN must be set "
                "when COMPLIANCE_REPORT_WORM_ARCHIVE_PROVIDER=external-http"
            )
        self._url = url
        self._token = token
        self._timeout_seconds = timeout_seconds

    def append(
        self, report: AuditComplianceReport, *, content_hash: str
    ) -> ComplianceReportArchiveRecord:
        payload = build_compliance_report_archive_payload(
            report=report,
            content_hash=content_hash,
        )
        response = httpx.post(
            self._url,
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=self._timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise ValueError("COMPLIANCE_REPORT_WORM_ARCHIVE_FAILED")
        body = response.json()
        record_id = str(body.get("record_id", "")).strip()
        sequence_number = body.get("sequence_number")
        if not record_id or not isinstance(sequence_number, int) or sequence_number < 1:
            raise ValueError("COMPLIANCE_REPORT_WORM_ARCHIVE_INVALID_RESPONSE")
        return ComplianceReportArchiveRecord(
            id=record_id,
            tenant_id=report.tenant_id,
            template=report.template,
            sequence_number=sequence_number,
            content_hash=content_hash,
            report_signature=report.report_signature,
            created_at=datetime.now(UTC),
        )


def build_compliance_report_archive_store(
    config: Settings = settings,
) -> ComplianceReportArchiveStore:
    if config.COMPLIANCE_REPORT_WORM_ARCHIVE_PROVIDER == "memory":
        return InMemoryComplianceReportArchiveStore()
    return ExternalHttpComplianceReportArchiveStore(
        url=config.COMPLIANCE_REPORT_WORM_ARCHIVE_URL,
        token=config.COMPLIANCE_REPORT_WORM_ARCHIVE_TOKEN,
        timeout_seconds=config.COMPLIANCE_REPORT_WORM_ARCHIVE_TIMEOUT_SECONDS,
    )


class AuditEventRepository:
    """进程内 append-only 审计事件仓库。

    当前仓库用于第一版 API 和测试闭环；后续可替换为 SQLAlchemy/WORM 实现，路由层不变。
    """

    def __init__(
        self,
        compliance_report_signer: ComplianceReportSigner | None = None,
        compliance_report_archive_store: ComplianceReportArchiveStore | None = None,
    ) -> None:
        self._events: list[AuditEvent] = []
        self._compliance_report_signer = compliance_report_signer or build_compliance_report_signer()
        self._compliance_report_archive_store = (
            compliance_report_archive_store or build_compliance_report_archive_store()
        )

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
            report_signature_algorithm=self._compliance_report_signer.algorithm,
            report_signature_key_id=self._compliance_report_signer.key_id,
            worm_storage_status="pending",
            worm_record_id="",
            worm_sequence_number=0,
            worm_content_hash="",
        )
        report.report_signature = self._compliance_report_signer.sign(report)
        archive_record = self._append_compliance_report_archive(report)
        report.worm_storage_status = "recorded"
        report.worm_record_id = archive_record.id
        report.worm_sequence_number = archive_record.sequence_number
        report.worm_content_hash = archive_record.content_hash
        return report

    def _append_compliance_report_archive(
        self, report: AuditComplianceReport
    ) -> ComplianceReportArchiveRecord:
        return self._compliance_report_archive_store.append(
            report,
            content_hash=calculate_compliance_report_content_hash(report),
        )

    def clear(self) -> None:
        self._events.clear()
        clear_archive = getattr(self._compliance_report_archive_store, "clear", None)
        if clear_archive is not None:
            clear_archive()


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
    return _sign_compliance_report_with_secret(report=report, secret=settings.SECRET_KEY)


def _sign_compliance_report_with_secret(*, report: AuditComplianceReport, secret: str) -> str:
    canonical = report.model_dump(
        mode="json",
        exclude={
            "report_signature",
            "report_signature_algorithm",
            "report_signature_key_id",
            "worm_storage_status",
            "worm_record_id",
            "worm_sequence_number",
            "worm_content_hash",
        },
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


def calculate_compliance_report_content_hash(report: AuditComplianceReport) -> str:
    canonical = report.model_dump(
        mode="json",
        exclude={
            "generated_at",
            "report_signature",
            "report_signature_algorithm",
            "report_signature_key_id",
            "worm_storage_status",
            "worm_record_id",
            "worm_sequence_number",
            "worm_content_hash",
        },
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_compliance_report_archive_payload(
    *, report: AuditComplianceReport, content_hash: str
) -> dict[str, Any]:
    return {
        "schema_version": "janusgate.audit-compliance-worm-archive.v1",
        "tenant_id": report.tenant_id,
        "template": report.template,
        "total": report.total,
        "event_ids": report.event_ids,
        "hash_chain_start": report.hash_chain_start,
        "hash_chain_end": report.hash_chain_end,
        "period_start": report.period_start.isoformat() if report.period_start else None,
        "period_end": report.period_end.isoformat() if report.period_end else None,
        "generated_at": report.generated_at.isoformat(),
        "report_signature": report.report_signature,
        "report_signature_algorithm": report.report_signature_algorithm,
        "report_signature_key_id": report.report_signature_key_id,
        "worm_content_hash": content_hash,
    }


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
