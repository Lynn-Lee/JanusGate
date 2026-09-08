"""Notification delivery retry and dead-letter worker."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.models.notification_channel import NotificationChannel
from app.models.webhook import NotificationDelivery, NotificationRule, WebhookEndpoint


@dataclass(frozen=True)
class NotificationDeliveryWorkerResult:
    processed: int = 0
    delivered: int = 0
    failed: int = 0
    dead_lettered: int = 0


class NotificationDeliverySender(ABC):
    """Sink contract used by the worker; concrete HTTP/IM providers live outside the queue."""

    @abstractmethod
    async def send(
        self,
        *,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        endpoint: WebhookEndpoint | None = None,
        channel: NotificationChannel | None = None,
    ) -> None:
        """Deliver one already-redacted notification payload."""


class HttpWebhookNotificationSender(NotificationDeliverySender):
    """HTTP sender for active webhook endpoints."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def send(
        self,
        *,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        endpoint: WebhookEndpoint | None = None,
        channel: NotificationChannel | None = None,
    ) -> None:
        del channel
        if endpoint is None:
            raise RuntimeError("webhook delivery missing endpoint")
        try:
            response = await self._client.post(
                endpoint.url,
                json={
                    "event_type": delivery.event_type,
                    "delivery_id": delivery.id,
                    "payload": payload,
                },
                headers={
                    "X-JanusGate-Event-Type": delivery.event_type,
                    "X-JanusGate-Tenant-Id": delivery.tenant_id,
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("webhook delivery transport failed") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"webhook delivery failed with status {response.status_code}")


class NotificationDeliveryWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        sender: NotificationDeliverySender,
        max_attempts: int = 3,
        retry_delay: timedelta = timedelta(minutes=5),
        batch_size: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        self._batch_size = batch_size

    async def run_due_once(self, *, now: datetime | None = None) -> NotificationDeliveryWorkerResult:
        effective_now = now or datetime.now(UTC)
        processed = delivered = failed = dead_lettered = 0

        async with self._session_factory() as session:
            rows = await self._load_due_deliveries(session=session, now=effective_now)
            for delivery, endpoint, channel in rows:
                processed += 1
                try:
                    bind_session = getattr(self._sender, "bind_session", None)
                    if callable(bind_session):
                        bind_session(session)
                    await self._sender.send(
                        endpoint=endpoint,
                        channel=channel,
                        delivery=delivery,
                        payload=_payload_dict(delivery.payload_json),
                    )
                except Exception as exc:
                    delivery.attempts += 1
                    delivery.last_error = str(exc)
                    delivery.next_attempt_at = effective_now + self._retry_delay
                    if delivery.attempts >= self._max_attempts:
                        delivery.status = "dead_letter"
                        dead_lettered += 1
                    else:
                        delivery.status = "failed"
                        failed += 1
                else:
                    delivery.attempts += 1
                    delivery.status = "delivered"
                    delivery.last_error = None
                    delivery.next_attempt_at = effective_now
                    delivered += 1
                delivery.updated_at = effective_now
            await session.commit()

        return NotificationDeliveryWorkerResult(
            processed=processed,
            delivered=delivered,
            failed=failed,
            dead_lettered=dead_lettered,
        )

    async def _load_due_deliveries(
        self, *, session: AsyncSession, now: datetime
    ) -> list[tuple[NotificationDelivery, WebhookEndpoint | None, NotificationChannel | None]]:
        channel_alias = aliased(NotificationChannel)
        result = await session.execute(
            select(NotificationDelivery, WebhookEndpoint, channel_alias)
            .join(
                NotificationRule,
                (NotificationRule.id == NotificationDelivery.notification_rule_id)
                & (NotificationRule.tenant_id == NotificationDelivery.tenant_id),
            )
            .outerjoin(
                WebhookEndpoint,
                (WebhookEndpoint.id == NotificationDelivery.webhook_endpoint_id)
                & (WebhookEndpoint.tenant_id == NotificationDelivery.tenant_id)
                & (WebhookEndpoint.status == "active"),
            )
            .outerjoin(
                channel_alias,
                (channel_alias.id == NotificationDelivery.channel_id)
                & (channel_alias.tenant_id == NotificationDelivery.tenant_id)
                & (channel_alias.status == "active"),
            )
            .where(
                NotificationDelivery.status.in_(("pending", "failed")),
                NotificationDelivery.next_attempt_at <= now,
                NotificationRule.status == "active",
                or_(
                    WebhookEndpoint.id.is_not(None),
                    channel_alias.id.is_not(None),
                ),
            )
            .order_by(NotificationDelivery.id)
            .limit(self._batch_size)
        )
        rows: list[
            tuple[NotificationDelivery, WebhookEndpoint | None, NotificationChannel | None]
        ] = []
        for delivery, endpoint, channel in result.all():
            rows.append((delivery, endpoint, channel))
        return rows


def _payload_dict(value: str) -> dict[str, object]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): item for key, item in parsed.items()}
