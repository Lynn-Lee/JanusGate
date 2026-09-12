"""#t75 通知渠道：官方 host 白名单、URL 去密、IM / SMS / 邮件 / 站内信投递。

约束：
- 渠道凭据只以 AES-256-GCM 落库，接口只暴露 `credential_configured`
- IM URL 不得把机器人 token 留在 query/路径里；token 剥离后写入加密字段
- 投递失败信息不得包含 payload、凭据或下游响应体
- HTTP 渠道强制 HTTPS；邮件本切片走 HTTPS 网关（SMTP 直连可后续切片）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
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

_IM_PATH_PLACEHOLDERS = frozenset({"", "hook", "v2", "bot", "open-apis", "services", "robot", "send"})


class ChannelConfigError(ValueError):
    """渠道 URL / 凭据不合法。对外映射为稳定错误码。"""


@dataclass(frozen=True)
class SanitizedChannel:
    channel_type: str
    url: str
    credential: str | None
    recipient: str | None


def sanitize_channel(
    *,
    channel_type: str,
    url: str,
    credential: str | None = None,
    recipient: str | None = None,
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
        )

    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ChannelConfigError("INVALID_WEBHOOK_URL")
    if parsed.username or parsed.password:
        raise ChannelConfigError("INVALID_WEBHOOK_URL")

    if kind in {"webhook", "sms", "email"}:
        if kind in {"sms", "email"}:
            secret = (credential or "").strip()
            if not secret:
                raise ChannelConfigError("CHANNEL_CREDENTIAL_REQUIRED")
            dest = (recipient or "").strip()
            if kind == "email" and "@" not in dest:
                raise ChannelConfigError("EMAIL_RECIPIENT_REQUIRED")
            if kind == "sms" and not dest:
                raise ChannelConfigError("SMS_RECIPIENT_REQUIRED")
        else:
            secret = (credential or "").strip() or None
            dest = (recipient or "").strip() or None
        return SanitizedChannel(
            channel_type=kind,
            url=urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path or "/",
                    parsed.params,
                    parsed.query,
                    "",
                )
            ),
            credential=secret,
            recipient=dest,
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
    )


def _extract_im_secret(kind: str, parsed: Any, credential: str | None) -> str:
    query = parse_qs(parsed.query)
    supplied = (credential or "").strip()
    token = ""
    if kind == "dingtalk":
        token = (query.get("access_token") or [""])[0].strip() or supplied
    elif kind == "wecom":
        token = (query.get("key") or [""])[0].strip() or supplied
    elif kind in {"feishu", "lark"}:
        token = parsed.path.rstrip("/").rsplit("/", 1)[-1].strip()
        if token.lower() in _IM_PATH_PLACEHOLDERS:
            token = supplied
        elif supplied:
            token = supplied
    else:
        rest = parsed.path.lstrip("/")
        if rest.startswith("services/"):
            token = rest[len("services/") :].strip("/")
        token = token or supplied
    if not token or token.lower() in _IM_PATH_PLACEHOLDERS:
        raise ChannelConfigError("CHANNEL_CREDENTIAL_REQUIRED")
    return token


def delivery_url(endpoint: WebhookEndpoint) -> str:
    """组装实际请求 URL。凭据只在内存中拼接，不回写数据库。"""
    kind = endpoint.channel_type or "webhook"
    secret = decrypt_field(endpoint.credential_encrypted or "") if endpoint.credential_encrypted else ""
    if kind == "dingtalk":
        return f"{_CANONICAL_URLS[kind]}?access_token={secret}"
    if kind == "wecom":
        return f"{_CANONICAL_URLS[kind]}?key={secret}"
    if kind in {"feishu", "lark", "slack"}:
        return f"{_CANONICAL_URLS[kind]}/{secret}"
    return endpoint.url


class ChannelNotificationSender(NotificationDeliverySender):
    """按 channel_type 分发。HTTP 失败统一成稳定错误，不泄露下游正文。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = http_client
        self._timeout_seconds = timeout_seconds

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

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
        await self._send_http(endpoint=endpoint, delivery=delivery, payload=payload, kind=kind)

    async def _send_inbox(
        self,
        *,
        session: AsyncSession,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        user_id = str((endpoint.recipient or "").strip())
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

    async def _send_http(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
        kind: str,
    ) -> None:
        body = _http_body(kind=kind, delivery=delivery, payload=payload, endpoint=endpoint)
        headers = {
            "X-JanusGate-Event-Type": delivery.event_type,
            "X-JanusGate-Tenant-Id": delivery.tenant_id,
        }
        if kind in {"sms", "email"}:
            token = decrypt_field(endpoint.credential_encrypted or "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        target = delivery_url(endpoint) if kind not in {"webhook", "sms", "email"} else endpoint.url
        try:
            response = await self._http().post(target, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{kind} delivery transport failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"{kind} delivery failed with status {response.status_code}")


def _http_body(
    *,
    kind: str,
    delivery: NotificationDelivery,
    payload: dict[str, object],
    endpoint: WebhookEndpoint,
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
    if kind == "sms":
        return {"to": endpoint.recipient or "", "text": f"{delivery.event_type}: {text}"}
    if kind == "email":
        return {
            "to": endpoint.recipient or "",
            "subject": delivery.event_type,
            "text": text,
        }
    return {
        "event_type": delivery.event_type,
        "delivery_id": delivery.id,
        "payload": payload,
    }


def event_types(value: str) -> list[str]:
    """解析 endpoint / rule 上的事件类型 JSON 列表。非法结构视为空列表。"""
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]
