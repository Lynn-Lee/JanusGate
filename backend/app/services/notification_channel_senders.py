"""#t75 notification channel senders (HTTPS IM + inbox) and delivery dispatcher."""
from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_channel import (
    CHANNEL_DINGTALK,
    CHANNEL_EMAIL,
    CHANNEL_FEISHU,
    CHANNEL_INBOX,
    CHANNEL_LARK,
    CHANNEL_SLACK,
    CHANNEL_SMS,
    CHANNEL_WEBHOOK,
    CHANNEL_WECOM,
    HTTPS_IM_CHANNEL_TYPES,
    InboxMessage,
    NotificationChannel,
)
from app.models.webhook import NotificationDelivery, WebhookEndpoint
from app.services.notification_delivery_worker import (
    HttpWebhookNotificationSender,
    NotificationDeliverySender,
)


def _require_https_url(url: str) -> str:
    normalized = (url or "").strip()
    if not normalized.lower().startswith("https://"):
        raise RuntimeError("channel webhook url must use https")
    return normalized


def _channel_config(channel: NotificationChannel) -> dict[str, Any]:
    try:
        parsed = json.loads(channel.config_json or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("channel config is invalid") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("channel config is invalid")
    return {str(key): value for key, value in parsed.items()}


def _message_text(*, delivery: NotificationDelivery, payload: dict[str, object]) -> str:
    summary = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if len(summary) > 1500:
        summary = summary[:1500] + "…"
    return f"[{delivery.event_type}] {summary}"


class ImWebhookNotificationSender:
    """HTTPS robot/incoming-webhook senders for DingTalk / Feishu / Lark / WeCom / Slack."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def send_channel(
        self,
        *,
        channel: NotificationChannel,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        if channel.channel_type not in HTTPS_IM_CHANNEL_TYPES | {CHANNEL_WEBHOOK}:
            raise RuntimeError(f"unsupported im channel type: {channel.channel_type}")
        config = _channel_config(channel)
        url = _require_https_url(str(config.get("webhook_url") or ""))
        body = self._build_body(
            channel_type=channel.channel_type, delivery=delivery, payload=payload
        )
        try:
            response = await self._client.post(
                url,
                json=body,
                headers={
                    "X-JanusGate-Event-Type": delivery.event_type,
                    "X-JanusGate-Tenant-Id": delivery.tenant_id,
                    "X-JanusGate-Channel-Type": channel.channel_type,
                },
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{channel.channel_type} delivery transport failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"{channel.channel_type} delivery failed with status {response.status_code}"
            )

    def _build_body(
        self,
        *,
        channel_type: str,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> dict[str, object]:
        text = _message_text(delivery=delivery, payload=payload)
        if channel_type == CHANNEL_SLACK:
            return {"text": text}
        if channel_type == CHANNEL_DINGTALK:
            return {"msgtype": "text", "text": {"content": text}}
        if channel_type in {CHANNEL_FEISHU, CHANNEL_LARK}:
            return {"msg_type": "text", "content": {"text": text}}
        if channel_type == CHANNEL_WECOM:
            return {"msgtype": "text", "text": {"content": text}}
        # Generic webhook-shaped channel config (channel_type=webhook).
        return {
            "event_type": delivery.event_type,
            "delivery_id": delivery.id,
            "payload": payload,
        }


class InboxNotificationSender:
    """DB-only inbox sink; no network I/O."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def send_channel(
        self,
        *,
        channel: NotificationChannel,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        if channel.channel_type != CHANNEL_INBOX:
            raise RuntimeError("inbox sender requires inbox channel")
        config = _channel_config(channel)
        user_id = str(
            config.get("user_id")
            or payload.get("user_id")
            or payload.get("subject_id")
            or ""
        ).strip()
        if not user_id:
            raise RuntimeError("inbox delivery missing user_id")
        title = str(payload.get("title") or delivery.event_type)[:255]
        self._session.add(
            InboxMessage(
                tenant_id=delivery.tenant_id,
                user_id=user_id,
                event_type=delivery.event_type,
                title=title,
                body_json=json.dumps(payload, sort_keys=True, default=str),
            )
        )


class StubChannelSender:
    """Fail-closed placeholders for SMS / email until providers are wired."""

    async def send_channel(
        self,
        *,
        channel: NotificationChannel,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        del delivery, payload
        if channel.channel_type == CHANNEL_SMS:
            raise RuntimeError("sms sender not configured")
        if channel.channel_type == CHANNEL_EMAIL:
            raise RuntimeError("email sender not configured")
        raise RuntimeError(f"channel type not implemented: {channel.channel_type}")


class ChannelAwareNotificationSender(NotificationDeliverySender):
    """Dispatcher: legacy WebhookEndpoint path + NotificationChannel path."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        webhook_sender: HttpWebhookNotificationSender | None = None,
        im_sender: ImWebhookNotificationSender | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._webhook_sender = webhook_sender or HttpWebhookNotificationSender(
            http_client=http_client
        )
        self._im_sender = im_sender or ImWebhookNotificationSender(http_client=http_client)
        self._stub_sender = StubChannelSender()
        self._session = session

    def bind_session(self, session: AsyncSession) -> None:
        self._session = session

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint | None = None,
        channel: NotificationChannel | None = None,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        if endpoint is not None:
            await self._webhook_sender.send(
                endpoint=endpoint, delivery=delivery, payload=payload
            )
            return
        if channel is None:
            raise RuntimeError("delivery target missing")
        if channel.channel_type == CHANNEL_INBOX:
            if self._session is None:
                raise RuntimeError("inbox sender requires database session")
            await InboxNotificationSender(session=self._session).send_channel(
                channel=channel, delivery=delivery, payload=payload
            )
            return
        if channel.channel_type in HTTPS_IM_CHANNEL_TYPES | {CHANNEL_WEBHOOK}:
            await self._im_sender.send_channel(
                channel=channel, delivery=delivery, payload=payload
            )
            return
        await self._stub_sender.send_channel(
            channel=channel, delivery=delivery, payload=payload
        )
