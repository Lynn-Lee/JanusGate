"""审计事件存储与 SIEM 投递服务首版。"""
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.api.audits.schemas import (
    AuditEvent,
    AuditEventCreate,
    AuditReportSummary,
    AuditSeverity,
    SiemDeliveryStatus,
)

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


class AuditEventRepository:
    """进程内 append-only 审计事件仓库。

    当前仓库用于第一版 API 和测试闭环；后续可替换为 SQLAlchemy/WORM 实现，路由层不变。
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

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

    def clear(self) -> None:
        self._events.clear()


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


def calculate_event_hash(event: AuditEvent) -> str:
    canonical = event.model_dump(mode="json", exclude={"event_hash", "siem_delivery_status", "siem_delivery_error", "siem_delivery_attempts", "siem_next_retry_at"})
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
