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

from app.core.security import decrypt_field
from app.models.webhook import (
    InAppMessage,
    NotificationDelivery,
    NotificationRule,
    SystemMessageSubscription,
    WebhookEndpoint,
)
from app.services.notification_channels import (
    CHANNEL_INBOX,
    CHANNEL_WEBHOOK,
    GATEWAY_CHANNEL_TYPES,
    IM_CHANNEL_TYPES,
    OUTBOUND_HTTP_CHANNEL_TYPES,
    build_delivery_url,
    channel_request_body,
)

_STABLE_TRANSPORT_ERROR = "notification delivery transport failed"
_STABLE_STATUS_ERROR = "notification delivery failed with status {status}"
_STABLE_CREDENTIAL_ERROR = "channel credential unavailable"
_STABLE_INBOX_RECIPIENT_ERROR = "inbox recipient required"


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
        db: AsyncSession | None = None,
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
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        db: AsyncSession | None = None,
    ) -> None:
        _ = db
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


class ChannelAwareNotificationSender(NotificationDeliverySender):
    """按 channel_type 分发：WebHook / IM / HTTPS 网关 / 站内信。

    错误信息只保留稳定状态码，不回写 payload、signing secret、机器人 token
    或下游响应体，以沿用 #t47 的脱敏与 dead-letter 契约。
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient | None = None,
        webhook_sender: HttpWebhookNotificationSender | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._session_factory = session_factory
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._webhook_sender = webhook_sender or HttpWebhookNotificationSender(
            http_client=self._client,
            timeout_seconds=timeout_seconds,
        )

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        db: AsyncSession | None = None,
    ) -> None:
        """按渠道投递已脱敏 payload；失败只抛稳定错误，不携带下游响应体。"""
        channel_type = endpoint.channel_type or CHANNEL_WEBHOOK
        if channel_type == CHANNEL_INBOX:
            await self._deliver_inbox(
                endpoint=endpoint, delivery=delivery, payload=payload, db=db
            )
            return
        if channel_type == CHANNEL_WEBHOOK:
            await self._webhook_sender.send(endpoint=endpoint, delivery=delivery, payload=payload)
            return
        if channel_type not in OUTBOUND_HTTP_CHANNEL_TYPES:
            raise RuntimeError(_STABLE_TRANSPORT_ERROR)
        await self._deliver_http(
            channel_type=channel_type,
            endpoint=endpoint,
            delivery=delivery,
            payload=payload,
        )

    async def _deliver_http(
        self,
        *,
        channel_type: str,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        credential = _decrypt_credential(endpoint.credential_encrypted)
        if channel_type in IM_CHANNEL_TYPES | GATEWAY_CHANNEL_TYPES and not credential:
            raise RuntimeError(_STABLE_CREDENTIAL_ERROR)
        url = build_delivery_url(
            channel_type=channel_type,
            stored_url=endpoint.url,
            credential=credential,
        )
        headers = {
            "X-JanusGate-Event-Type": delivery.event_type,
            "X-JanusGate-Tenant-Id": delivery.tenant_id,
        }
        if channel_type in GATEWAY_CHANNEL_TYPES:
            headers["Authorization"] = f"Bearer {credential}"
        body = channel_request_body(
            channel_type=channel_type,
            event_type=delivery.event_type,
            delivery_id=delivery.id,
            payload=payload,
        )
        try:
            response = await self._client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(_STABLE_TRANSPORT_ERROR) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(_STABLE_STATUS_ERROR.format(status=response.status_code))

    async def _deliver_inbox(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        db: AsyncSession | None = None,
    ) -> None:
        recipient = (delivery.recipient_user_id or "").strip()
        if not recipient:
            raise RuntimeError(_STABLE_INBOX_RECIPIENT_ERROR)
        message = InAppMessage(
            tenant_id=delivery.tenant_id,
            user_id=recipient,
            event_type=delivery.event_type,
            title=delivery.event_type,
            body_json=json.dumps(payload, sort_keys=True, default=str),
            delivery_id=delivery.id,
        )
        if db is not None:
            db.add(message)
            await db.flush()
            return
        async with self._session_factory() as session:
            session.add(message)
            await session.commit()


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
                        payload=_payload_dict(delivery.payload_json),
                        db=session,
                    )
                except Exception as exc:
                    delivery.attempts += 1
                    delivery.last_error = _stable_error(exc)
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
        result = await session.execute(
            select(NotificationDelivery, WebhookEndpoint)
            .join(
                WebhookEndpoint,
                (WebhookEndpoint.id == NotificationDelivery.webhook_endpoint_id)
                & (WebhookEndpoint.tenant_id == NotificationDelivery.tenant_id),
            )
            .outerjoin(
                NotificationRule,
                (NotificationRule.id == NotificationDelivery.notification_rule_id)
                & (NotificationRule.tenant_id == NotificationDelivery.tenant_id),
            )
            .outerjoin(
                SystemMessageSubscription,
                (SystemMessageSubscription.id == NotificationDelivery.subscription_id)
                & (SystemMessageSubscription.tenant_id == NotificationDelivery.tenant_id),
            )
            .where(
                NotificationDelivery.status.in_(("pending", "failed")),
                NotificationDelivery.next_attempt_at <= now,
                WebhookEndpoint.status == "active",
                or_(
                    NotificationRule.status == "active",
                    SystemMessageSubscription.status == "active",
                ),
            )
            .order_by(NotificationDelivery.id)
            .limit(self._batch_size)
        )
        return [(delivery, endpoint) for delivery, endpoint in result.all()]


def _payload_dict(value: str) -> dict[str, object]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): item for key, item in parsed.items()}


def _decrypt_credential(value: str | None) -> str | None:
    if not value:
        return None
    decrypted = decrypt_field(value).strip()
    return decrypted or None


def _stable_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if any(part in lowered for part in ("password", "secret", "token", "bearer", "access_token")):
        return _STABLE_TRANSPORT_ERROR
    return message
