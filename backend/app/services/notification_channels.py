"""#t75 通知渠道 sender：IM / SMS / 邮件 / 站内信。

只接收已脱敏 payload。错误信息不包含 payload、URL query、signing secret 或下游响应体。
HTTP 渠道强制 HTTPS，IM 渠道再限制官方 host，避免把机器人 token 打到任意 URL。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import decrypt_field
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_delivery_worker import (
    HttpWebhookNotificationSender,
    NotificationDeliverySender,
)

CHANNEL_WEBHOOK = "webhook"
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_FEISHU = "feishu"
CHANNEL_LARK = "lark"
CHANNEL_WECOM = "wecom"
CHANNEL_SLACK = "slack"
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_INBOX = "inbox"

HTTP_IM_CHANNELS = {
    CHANNEL_DINGTALK,
    CHANNEL_FEISHU,
    CHANNEL_LARK,
    CHANNEL_WECOM,
    CHANNEL_SLACK,
}

ALLOWED_CHANNEL_TYPES = frozenset(
    {
        CHANNEL_WEBHOOK,
        CHANNEL_DINGTALK,
        CHANNEL_FEISHU,
        CHANNEL_LARK,
        CHANNEL_WECOM,
        CHANNEL_SLACK,
        CHANNEL_SMS,
        CHANNEL_EMAIL,
        CHANNEL_INBOX,
    }
)

_IM_ALLOWED_HOSTS: dict[str, frozenset[str]] = {
    CHANNEL_DINGTALK: frozenset({"oapi.dingtalk.com"}),
    CHANNEL_FEISHU: frozenset({"open.feishu.cn"}),
    CHANNEL_LARK: frozenset({"open.larksuite.com"}),
    CHANNEL_WECOM: frozenset({"qyapi.weixin.qq.com"}),
    CHANNEL_SLACK: frozenset({"hooks.slack.com"}),
}

INBOX_SENTINEL_URL = "inbox://local"


class ChannelDeliveryError(RuntimeError):
    """稳定、不含敏感字段的渠道投递失败。"""


def public_channel_url(url: str) -> str:
    """响应与日志用的 URL：去掉 userinfo 与 query，避免泄露机器人 token。"""
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def validate_channel_endpoint(*, channel_type: str, url: str) -> None:
    """创建渠道时 fail-closed 校验 URL scheme / host。"""
    if channel_type not in ALLOWED_CHANNEL_TYPES:
        raise ValueError("INVALID_NOTIFICATION_CHANNEL")
    parsed = urlsplit(url)
    if channel_type == CHANNEL_INBOX:
        if url != INBOX_SENTINEL_URL:
            raise ValueError("INVALID_INBOX_URL")
        return
    if channel_type == CHANNEL_EMAIL:
        if parsed.scheme not in {"smtp", "smtps"} or not parsed.hostname:
            raise ValueError("INVALID_EMAIL_URL")
        return
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("INVALID_WEBHOOK_URL")
    allowed = _IM_ALLOWED_HOSTS.get(channel_type)
    if allowed is not None and parsed.hostname not in allowed:
        raise ValueError("INVALID_CHANNEL_HOST")


def render_notification_text(*, event_type: str, payload: dict[str, object]) -> str:
    """把已脱敏 payload 压成单行文本，供 IM / SMS / 邮件正文使用。"""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"{event_type} {body}"


class InboxNotificationSender:
    """把站内信写入当前租户收件箱，不走外网。"""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        user_id = (delivery.recipient_user_id or endpoint.target or "").strip()
        if not user_id:
            raise ChannelDeliveryError("inbox delivery missing recipient")
        async with self._session_factory() as session:
            if delivery.id is not None:
                existing = await session.execute(
                    select(InAppMessage).where(InAppMessage.delivery_id == delivery.id)
                )
                if existing.scalar_one_or_none() is not None:
                    return
            session.add(
                InAppMessage(
                    tenant_id=delivery.tenant_id,
                    user_id=user_id,
                    delivery_id=delivery.id,
                    event_type=delivery.event_type,
                    title=delivery.event_type,
                    body_json=json.dumps(payload, sort_keys=True, default=str),
                )
            )
            await session.commit()


class SmtpNotificationSender:
    """SMTP 邮件 sender。只允许 SMTPS 或 STARTTLS，凭据仅内存解密。"""

    def send_message(
        self,
        *,
        host: str,
        port: int,
        use_ssl: bool,
        username: str,
        password: str,
        mail_from: str,
        mail_to: str,
        subject: str,
        body: str,
    ) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = mail_from
        message["To"] = mail_to
        message.set_content(body)
        context = ssl.create_default_context()
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as client:
                if username:
                    client.login(username, password)
                client.send_message(message)
            return
        with smtplib.SMTP(host, port, timeout=10) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)


class ChannelNotificationSender(NotificationDeliverySender):
    """按 channel_type 分发到 WebHook / IM / SMS / 邮件 / 站内信。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        smtp_sender: SmtpNotificationSender | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._webhook = HttpWebhookNotificationSender(http_client=self._client)
        self._inbox = (
            InboxNotificationSender(session_factory=session_factory)
            if session_factory is not None
            else None
        )
        self._smtp = smtp_sender or SmtpNotificationSender()

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        channel_type = (endpoint.channel_type or CHANNEL_WEBHOOK).strip() or CHANNEL_WEBHOOK
        if channel_type not in ALLOWED_CHANNEL_TYPES:
            raise ChannelDeliveryError("unsupported notification channel")
        if channel_type == CHANNEL_WEBHOOK:
            await self._webhook.send(endpoint=endpoint, delivery=delivery, payload=payload)
            return
        if channel_type == CHANNEL_INBOX:
            if self._inbox is None:
                raise ChannelDeliveryError("inbox sender is not configured")
            await self._inbox.send(endpoint=endpoint, delivery=delivery, payload=payload)
            return
        if channel_type == CHANNEL_EMAIL:
            await self._send_email(endpoint=endpoint, delivery=delivery, payload=payload)
            return
        await self._send_http_channel(
            channel_type=channel_type,
            endpoint=endpoint,
            delivery=delivery,
            payload=payload,
        )

    async def _send_http_channel(
        self,
        *,
        channel_type: str,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        try:
            validate_channel_endpoint(channel_type=channel_type, url=endpoint.url)
        except ValueError as exc:
            raise ChannelDeliveryError("channel host is not allowed") from exc
        text = render_notification_text(event_type=delivery.event_type, payload=payload)
        url = endpoint.url
        headers: dict[str, str] = {}
        body: dict[str, Any]
        if channel_type == CHANNEL_DINGTALK:
            url = _dingtalk_signed_url(url, endpoint.credential_encrypted)
            body = {"msgtype": "text", "text": {"content": text}}
        elif channel_type in {CHANNEL_FEISHU, CHANNEL_LARK}:
            body = {"msg_type": "text", "content": {"text": text}}
        elif channel_type == CHANNEL_WECOM:
            body = {"msgtype": "text", "text": {"content": text}}
        elif channel_type == CHANNEL_SLACK:
            body = {"text": text}
        else:
            credential = _decrypt_credential(endpoint.credential_encrypted)
            if credential:
                headers["Authorization"] = f"Bearer {credential}"
            body = {
                "event_type": delivery.event_type,
                "delivery_id": delivery.id,
                "to": endpoint.target,
                "payload": payload,
            }
        try:
            response = await self._client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ChannelDeliveryError("channel delivery transport failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ChannelDeliveryError(
                f"channel delivery failed with status {response.status_code}"
            )

    async def _send_email(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        parsed = urlsplit(endpoint.url)
        if parsed.scheme not in {"smtp", "smtps"} or not parsed.hostname:
            raise ChannelDeliveryError("channel host is not allowed")
        mail_to = (endpoint.target or "").strip()
        if not mail_to or "@" not in mail_to:
            raise ChannelDeliveryError("email delivery missing recipient")
        username = parsed.username or mail_to
        password = _decrypt_credential(endpoint.credential_encrypted)
        port = parsed.port or (465 if parsed.scheme == "smtps" else 587)
        body = render_notification_text(event_type=delivery.event_type, payload=payload)
        try:
            self._smtp.send_message(
                host=parsed.hostname,
                port=port,
                use_ssl=parsed.scheme == "smtps",
                username=username,
                password=password,
                mail_from=username,
                mail_to=mail_to,
                subject=delivery.event_type,
                body=body,
            )
        except (OSError, smtplib.SMTPException) as exc:
            raise ChannelDeliveryError("email delivery transport failed") from exc


def _decrypt_credential(value: str) -> str:
    if not (value or "").strip():
        return ""
    return decrypt_field(value)


def _dingtalk_signed_url(url: str, credential_encrypted: str) -> str:
    """钉钉自定义机器人加签：timestamp + HMAC-SHA256，secret 不进入错误信息。"""
    secret = _decrypt_credential(credential_encrypted)
    if not secret:
        return url
    timestamp = str(int(datetime.now(UTC).timestamp() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = base64.b64encode(digest).decode("ascii")
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["timestamp"] = timestamp
    query["sign"] = sign
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
