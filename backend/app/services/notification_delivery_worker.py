"""Notification delivery retry and dead-letter worker."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.models.webhook import (
    NotificationDelivery,
    NotificationRule,
    SystemMessageSubscription,
    WebhookEndpoint,
)
from app.services.notification_payload import payload_dict


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
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        session: AsyncSession | None = None,
    ) -> None:
        """Deliver one already-redacted notification payload.

        ``session`` 仅站内信需要，用于在同一事务写入 `InAppMessage`。
        """


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
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        session: AsyncSession | None = None,
    ) -> None:
        del session
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
            for delivery, endpoint in rows:
                processed += 1
                try:
                    await self._sender.send(
                        endpoint=endpoint,
                        delivery=delivery,
                        payload=payload_dict(delivery.payload_json),
                        session=session,
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
    ) -> list[tuple[NotificationDelivery, WebhookEndpoint]]:
        rule = aliased(NotificationRule)
        subscription = aliased(SystemMessageSubscription)
        result = await session.execute(
            select(NotificationDelivery, WebhookEndpoint)
            .join(
                WebhookEndpoint,
                (WebhookEndpoint.id == NotificationDelivery.webhook_endpoint_id)
                & (WebhookEndpoint.tenant_id == NotificationDelivery.tenant_id),
            )
            .outerjoin(
                rule,
                (rule.id == NotificationDelivery.notification_rule_id)
                & (rule.tenant_id == NotificationDelivery.tenant_id),
            )
            .outerjoin(
                subscription,
                (subscription.id == NotificationDelivery.subscription_id)
                & (subscription.tenant_id == NotificationDelivery.tenant_id),
            )
            .where(
                NotificationDelivery.status.in_(("pending", "failed")),
                NotificationDelivery.next_attempt_at <= now,
                WebhookEndpoint.status == "active",
                or_(
                    rule.status == "active",
                    subscription.status == "active",
                ),
            )
            .order_by(NotificationDelivery.id)
            .limit(self._batch_size)
        )
        return [(delivery, endpoint) for delivery, endpoint in result.all()]
