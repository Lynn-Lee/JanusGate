"""#t75 通知渠道：官方 host 白名单、URL 去密、IM / SMS / 邮件 / 站内信投递。

约束：
- 渠道凭据只以 AES-256-GCM 落库，接口只暴露 `credential_configured`
- IM URL 不得带 query，机器人 token 从路径/query 剥离后写入加密字段
- 投递失败信息不得包含 payload、凭据或下游响应体
"""
from __future__ import annotations

import json
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_field
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_delivery_worker import NotificationDeliverySender
from app.services.notification_redact import payload_summary

CHANNEL_TYPES = (
    "webhook",
    "dingtalk",
    "feishu",
    "lark",
    "wecom",
    "slack",
    "sms",
    "email",
    "inbox",
)

_IM_HOSTS: dict[str, frozenset[str]] = {
    "dingtalk": frozenset({"oapi.dingtalk.com"}),
    "feishu": frozenset({"open.feishu.cn"}),
    "lark": frozenset({"open.larksuite.com"}),
    "wecom": frozenset({"qyapi.weixin.qq.com"}),
    "slack": frozenset({"hooks.slack.com", "slack.com"}),
}

_CANONICAL_URLS = {
    "dingtalk": "https://oapi.dingtalk.com/robot/send",
    "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook",
    "lark": "https://open.larksuite.com/open-apis/bot/v2/hook",
    "wecom": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
    "slack": "https://hooks.slack.com/services",
}


class ChannelConfigError(ValueError):
    """渠道 URL / 凭据不合法。对外映射为稳定错误码。"""


@dataclass(frozen=True)
class SanitizedChannel:
    channel_type: str
    url: str
    credential: str | None
    recipient: str | None
    auth_username: str | None


def sanitize_channel(
    *,
    channel_type: str,
    url: str,
    credential: str | None = None,
    recipient: str | None = None,
    auth_username: str | None = None,
) -> SanitizedChannel:
    """校验渠道并剥离 URL 中的机器人 token。失败时抛出 ChannelConfigError。"""
    kind = channel_type.strip().lower()
    if kind not in CHANNEL_TYPES:
        raise ChannelConfigError("INVALID_CHANNEL_TYPE")

    if kind == "inbox":
        user_id = (recipient or "").strip()
        if not user_id:
            raise ChannelConfigError("INBOX_RECIPIENT_REQUIRED")
        return SanitizedChannel(
            channel_type=kind,
            url="inbox://local",
            credential=None,
            recipient=user_id,
            auth_username=None,
        )

    if kind == "email":
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"smtp", "smtps"}:
            raise ChannelConfigError("INVALID_EMAIL_URL")
        if parsed.username or parsed.password or parsed.query:
            raise ChannelConfigError("INVALID_EMAIL_URL")
        if not parsed.hostname:
            raise ChannelConfigError("INVALID_EMAIL_URL")
        to_addr = (recipient or "").strip()
        if "@" not in to_addr:
            raise ChannelConfigError("EMAIL_RECIPIENT_REQUIRED")
        secret = (credential or "").strip()
        if not secret:
            raise ChannelConfigError("CHANNEL_CREDENTIAL_REQUIRED")
        port = parsed.port or (465 if parsed.scheme == "smtps" else 587)
        canonical = f"{parsed.scheme}://{parsed.hostname}:{port}"
        return SanitizedChannel(
            channel_type=kind,
            url=canonical,
            credential=secret,
            recipient=to_addr,
            auth_username=(auth_username or to_addr).strip() or to_addr,
        )

    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ChannelConfigError("INVALID_WEBHOOK_URL")
    if parsed.username or parsed.password:
        raise ChannelConfigError("INVALID_WEBHOOK_URL")

    if kind == "webhook" or kind == "sms":
        if parsed.query:
            raise ChannelConfigError("CHANNEL_URL_QUERY_FORBIDDEN")
        secret = (credential or "").strip() or None
        if kind == "sms" and not secret:
            raise ChannelConfigError("CHANNEL_CREDENTIAL_REQUIRED")
        return SanitizedChannel(
            channel_type=kind,
            url=urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", "")),
            credential=secret,
            recipient=(recipient or "").strip() or None,
            auth_username=None,
        )

    allowed = _IM_HOSTS[kind]
    if parsed.hostname.lower() not in allowed:
        raise ChannelConfigError("CHANNEL_HOST_NOT_ALLOWED")
    extracted = _extract_im_secret(kind, parsed, credential)
    return SanitizedChannel(
        channel_type=kind,
        url=_CANONICAL_URLS[kind],
        credential=extracted,
        recipient=None,
        auth_username=None,
    )


def _extract_im_secret(kind: str, parsed: Any, credential: str | None) -> str:
    query = parse_qs(parsed.query)
    supplied = (credential or "").strip()
    if kind == "dingtalk":
        token = (query.get("access_token") or [""])[0].strip() or supplied
    elif kind == "wecom":
        token = (query.get("key") or [""])[0].strip() or supplied
    elif kind in {"feishu", "lark"}:
        token = parsed.path.rstrip("/").rsplit("/", 1)[-1].strip()
        if token in {"hook", "v2", "bot", "open-apis", ""}:
            token = supplied
        elif supplied and token != supplied and parsed.path.rstrip("/").endswith("/hook"):
            token = supplied
    else:
        rest = parsed.path.lstrip("/")
        if rest.startswith("services/"):
            token = rest[len("services/") :].strip("/")
        else:
            token = supplied
        if not token:
            token = supplied
    if not token:
        raise ChannelConfigError("CHANNEL_CREDENTIAL_REQUIRED")
    return token


def delivery_url(endpoint: WebhookEndpoint) -> str:
    """组装实际请求 URL。凭据只在内存中拼接，不回写数据库。"""
    kind = endpoint.channel_type
    secret = decrypt_field(endpoint.credential_encrypted or "") if endpoint.credential_encrypted else ""
    if kind == "dingtalk":
        return f"{_CANONICAL_URLS[kind]}?access_token={secret}"
    if kind == "wecom":
        return f"{_CANONICAL_URLS[kind]}?key={secret}"
    if kind in {"feishu", "lark"}:
        return f"{_CANONICAL_URLS[kind]}/{secret}"
    if kind == "slack":
        return f"{_CANONICAL_URLS[kind]}/{secret}"
    return endpoint.url


class SmtpMailSender(ABC):
    """邮件投递边界。生产用 SMTPS / STARTTLS；测试注入假实现。"""

    @abstractmethod
    async def send_mail(
        self,
        *,
        host: str,
        port: int,
        use_ssl: bool,
        username: str,
        password: str,
        to_addr: str,
        subject: str,
        body: str,
    ) -> None:
        """发送一封纯文本邮件。失败必须抛出且不得带上 password。"""


class StdlibSmtpMailSender(SmtpMailSender):
    """本地 smtplib adapter：smtps 走 SMTP_SSL，smtp 强制 STARTTLS。"""

    async def send_mail(
        self,
        *,
        host: str,
        port: int,
        use_ssl: bool,
        username: str,
        password: str,
        to_addr: str,
        subject: str,
        body: str,
    ) -> None:
        def _send() -> None:
            message = EmailMessage()
            message["To"] = to_addr
            message["From"] = username
            message["Subject"] = subject
            message.set_content(body)
            context = ssl.create_default_context()
            try:
                if use_ssl:
                    with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as client:
                        client.login(username, password)
                        client.send_message(message)
                else:
                    with smtplib.SMTP(host, port, timeout=10) as client:
                        client.starttls(context=context)
                        client.login(username, password)
                        client.send_message(message)
            except smtplib.SMTPException as exc:
                raise RuntimeError("email delivery failed") from exc

        import asyncio

        await asyncio.to_thread(_send)


class ChannelNotificationSender(NotificationDeliverySender):
    """按 channel_type 分发。HTTP 失败统一成稳定错误，不泄露下游正文。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        mail_sender: SmtpMailSender | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._mail = mail_sender or StdlibSmtpMailSender()

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        session: AsyncSession | None = None,
    ) -> None:
        kind = endpoint.channel_type or "webhook"
        if kind == "inbox":
            if session is None:
                raise RuntimeError("inbox delivery requires a database session")
            await self._send_inbox(
                session=session, endpoint=endpoint, delivery=delivery, payload=payload
            )
            return
        if kind == "email":
            await self._send_email(endpoint=endpoint, delivery=delivery, payload=payload)
            return
        await self._send_http(endpoint=endpoint, delivery=delivery, payload=payload, kind=kind)

    async def _send_inbox(
        self,
        *,
        session: AsyncSession,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        user_id = (endpoint.recipient or "").strip()
        if not user_id:
            raise RuntimeError("inbox delivery missing recipient")
        session.add(
            InAppMessage(
                tenant_id=delivery.tenant_id,
                user_id=user_id,
                event_type=delivery.event_type,
                title=delivery.event_type,
                body=payload_summary(payload),
                delivery_id=delivery.id,
            )
        )

    async def _send_email(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        parsed = urlparse(endpoint.url)
        password = decrypt_field(endpoint.credential_encrypted or "")
        username = (endpoint.auth_username or endpoint.recipient or "").strip()
        to_addr = (endpoint.recipient or "").strip()
        if not password or not username or not to_addr or not parsed.hostname:
            raise RuntimeError("email delivery missing configuration")
        await self._mail.send_mail(
            host=parsed.hostname,
            port=parsed.port or (465 if parsed.scheme == "smtps" else 587),
            use_ssl=parsed.scheme == "smtps",
            username=username,
            password=password,
            to_addr=to_addr,
            subject=delivery.event_type,
            body=payload_summary(payload),
        )

    async def _send_http(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        kind: str,
    ) -> None:
        body = _http_body(kind=kind, delivery=delivery, payload=payload)
        headers = {
            "X-JanusGate-Event-Type": delivery.event_type,
            "X-JanusGate-Tenant-Id": delivery.tenant_id,
        }
        if kind == "sms":
            token = decrypt_field(endpoint.credential_encrypted or "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self._client.post(
                delivery_url(endpoint) if kind != "webhook" and kind != "sms" else endpoint.url,
                json=body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{kind} delivery transport failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"{kind} delivery failed with status {response.status_code}")


def _http_body(
    *, kind: str, delivery: NotificationDelivery, payload: dict[str, object]
) -> dict[str, object]:
    text = payload_summary(payload)
    if kind == "dingtalk":
        return {"msgtype": "text", "text": {"content": f"{delivery.event_type}: {text}"}}
    if kind in {"feishu", "lark"}:
        return {"msg_type": "text", "content": {"text": f"{delivery.event_type}: {text}"}}
    if kind == "wecom":
        return {"msgtype": "text", "text": {"content": f"{delivery.event_type}: {text}"}}
    if kind == "slack":
        return {"text": f"{delivery.event_type}: {text}"}
    return {
        "event_type": delivery.event_type,
        "delivery_id": delivery.id,
        "payload": payload,
    }


def event_types(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]
