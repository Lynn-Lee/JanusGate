"""按 channel_type 分发的通知 sender：IM / SMS / 邮件 / 站内信 / WebHook。

约束：只接收已脱敏 payload；错误信息不得包含 payload、凭据或下游响应体。
"""
from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_field
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_channels import NotificationChannelType
from app.services.notification_delivery_worker import NotificationDeliverySender
from app.services.notification_payload import config_dict


class SmtpTransport(Protocol):
    """可替换的 SMTP 发送面，测试注入假实现，避免真实网络。"""

    def send(self, *, host: str, port: int, use_ssl: bool, username: str, password: str, message: EmailMessage) -> None:
        """发送一封已构造的邮件。失败应抛出异常。"""


class StdlibSmtpTransport:
    """使用 SMTPS 或 STARTTLS 投递；明文 25 端口不在此实现。"""

    def send(
        self,
        *,
        host: str,
        port: int,
        use_ssl: bool,
        username: str,
        password: str,
        message: EmailMessage,
    ) -> None:
        client: smtplib.SMTP | smtplib.SMTP_SSL
        if use_ssl:
            client = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            client = smtplib.SMTP(host, port, timeout=10)
        try:
            if not use_ssl:
                client.starttls()
            if username:
                client.login(username, password)
            client.send_message(message)
        finally:
            client.quit()


class ChannelDispatchSender(NotificationDeliverySender):
    """根据 endpoint.channel_type 选择具体 sender。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
        smtp_transport: SmtpTransport | None = None,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._smtp = smtp_transport or StdlibSmtpTransport()

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        session: AsyncSession | None = None,
    ) -> None:
        channel = NotificationChannelType(endpoint.channel_type)
        if channel is NotificationChannelType.INBOX:
            await self._send_inbox(
                endpoint=endpoint, delivery=delivery, payload=payload, session=session
            )
            return
        if channel is NotificationChannelType.EMAIL:
            await self._send_email(endpoint=endpoint, delivery=delivery, payload=payload)
            return
        body = _channel_body(channel=channel, delivery=delivery, payload=payload)
        try:
            response = await self._client.post(
                endpoint.url,
                json=body,
                headers=_channel_headers(channel=channel, endpoint=endpoint, delivery=delivery),
            )
        except httpx.HTTPError as exc:
            raise RuntimeError("notification delivery transport failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"notification delivery failed with status {response.status_code}")

    async def _send_inbox(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        session: AsyncSession | None,
    ) -> None:
        del endpoint
        user_id = delivery.recipient_user_id
        if not user_id:
            raise RuntimeError("inbox delivery missing recipient")
        if session is None:
            raise RuntimeError("inbox delivery requires active session")
        title = str(payload.get("title") or delivery.event_type)
        body = str(payload.get("message") or payload.get("body") or json.dumps(payload, ensure_ascii=False))
        session.add(
            InAppMessage(
                tenant_id=delivery.tenant_id,
                user_id=user_id,
                delivery_id=delivery.id,
                event_type=delivery.event_type,
                title=title[:200],
                body=body,
            )
        )

    async def _send_email(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        config = config_dict(endpoint.config_json)
        to_address = str(config.get("to_address") or "")
        from_address = str(config.get("from_address") or "")
        if not to_address or not from_address:
            raise RuntimeError("email channel missing from/to address")
        parts = urlsplit(endpoint.url)
        host = parts.hostname or ""
        use_ssl = parts.scheme == "smtps"
        port = parts.port or (465 if use_ssl else 587)
        username = str(config.get("smtp_username") or "")
        password = decrypt_field(endpoint.credential_encrypted or "")
        message = EmailMessage()
        message["From"] = from_address
        message["To"] = to_address
        message["Subject"] = str(payload.get("title") or delivery.event_type)
        message.set_content(str(payload.get("message") or json.dumps(payload, ensure_ascii=False)))
        try:
            self._smtp.send(
                host=host,
                port=port,
                use_ssl=use_ssl,
                username=username,
                password=password,
                message=message,
            )
        except Exception as exc:
            raise RuntimeError("email delivery transport failed") from exc


def _channel_headers(
    *,
    channel: NotificationChannelType,
    endpoint: WebhookEndpoint,
    delivery: NotificationDelivery,
) -> dict[str, str]:
    headers = {
        "X-JanusGate-Event-Type": delivery.event_type,
        "X-JanusGate-Tenant-Id": delivery.tenant_id,
    }
    token = decrypt_field(endpoint.credential_encrypted or "")
    if token and channel is NotificationChannelType.SMS:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _channel_body(
    *,
    channel: NotificationChannelType,
    delivery: NotificationDelivery,
    payload: dict[str, object],
) -> dict[str, object]:
    text = str(payload.get("message") or payload.get("title") or delivery.event_type)
    if channel is NotificationChannelType.DINGTALK:
        return {"msgtype": "text", "text": {"content": text}}
    if channel in {NotificationChannelType.FEISHU, NotificationChannelType.LARK}:
        return {"msg_type": "text", "content": {"text": text}}
    if channel is NotificationChannelType.WECOM:
        return {"msgtype": "text", "text": {"content": text}}
    if channel is NotificationChannelType.SLACK:
        return {"text": text}
    if channel is NotificationChannelType.SMS:
        return {"text": text, "event_type": delivery.event_type}
    return {
        "event_type": delivery.event_type,
        "delivery_id": delivery.id,
        "payload": payload,
    }
