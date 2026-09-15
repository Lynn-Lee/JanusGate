"""#t75 通知渠道：官方 IM host、凭据剥离加密、HTTPS 网关与站内信投递。

约束：沿用 #t47 脱敏 payload 与 dead-letter；错误不得包含 payload、凭据或下游响应体。
邮件/短信本切片只走 HTTPS 网关 Bearer，不直连 SMTP。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import decrypt_field, encrypt_field
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_delivery_worker import NotificationDeliverySender

CHANNEL_WEBHOOK = "webhook"
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_FEISHU = "feishu"
CHANNEL_LARK = "lark"
CHANNEL_WECOM = "wecom"
CHANNEL_SLACK = "slack"
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_INBOX = "inbox"

IM_CHANNELS = frozenset(
    {CHANNEL_DINGTALK, CHANNEL_FEISHU, CHANNEL_LARK, CHANNEL_WECOM, CHANNEL_SLACK}
)
GATEWAY_CHANNELS = frozenset({CHANNEL_SMS, CHANNEL_EMAIL})
ALL_CHANNELS = frozenset(
    {CHANNEL_WEBHOOK, CHANNEL_INBOX} | IM_CHANNELS | GATEWAY_CHANNELS
)

OFFICIAL_HOSTS: dict[str, frozenset[str]] = {
    CHANNEL_DINGTALK: frozenset({"oapi.dingtalk.com"}),
    CHANNEL_FEISHU: frozenset({"open.feishu.cn"}),
    CHANNEL_LARK: frozenset({"open.larksuite.com"}),
    CHANNEL_WECOM: frozenset({"qyapi.weixin.qq.com"}),
    CHANNEL_SLACK: frozenset({"hooks.slack.com"}),
}

_SECRET_QUERY_KEYS = frozenset(
    {"access_token", "token", "key", "sign", "secret", "password"}
)
_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|credential)\s*=\s*[^\s,;]+"
)
_STABLE_TRANSPORT_ERROR = "channel delivery transport failed"
_STABLE_STATUS_ERROR = "channel delivery failed with status {status}"
_INBOX_MISSING_RECIPIENT = "inbox delivery missing recipient"
_INBOX_STORE_UNAVAILABLE = "inbox store unavailable"


class ChannelValidationError(ValueError):
    """渠道 URL / 凭据校验失败，`args[0]` 为稳定业务错误码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SanitizedChannelTarget:
    """对外可回显的 URL 与仅内存/落库密文的凭据。"""

    public_url: str
    credential: str | None


def redact_notification_payload(value: Any) -> Any:
    """递归脱敏敏感键名与 `token=` 赋值片段，供入库与扇出复用。"""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_notification_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_notification_payload(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
    return value


def parse_event_types(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def sanitize_channel_target(
    *,
    channel_type: str,
    url: str | None,
    credential: str | None = None,
) -> SanitizedChannelTarget:
    """校验渠道 URL、剥离机器人 token，并返回可回显的公共 URL。

    失败模式：非 HTTPS / 带 userinfo → `INVALID_WEBHOOK_URL`；
    IM 非官方 host → `CHANNEL_HOST_NOT_ALLOWED`；
    IM/网关缺凭据 → `CHANNEL_CREDENTIAL_REQUIRED`。
    """

    if channel_type not in ALL_CHANNELS:
        raise ChannelValidationError("INVALID_CHANNEL_TYPE")
    if channel_type == CHANNEL_INBOX:
        return SanitizedChannelTarget(public_url="", credential=None)

    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ChannelValidationError("INVALID_WEBHOOK_URL")
    if parsed.username or parsed.password:
        raise ChannelValidationError("INVALID_WEBHOOK_URL")

    host = parsed.hostname.lower()
    allowed_hosts = OFFICIAL_HOSTS.get(channel_type)
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ChannelValidationError("CHANNEL_HOST_NOT_ALLOWED")

    query_pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    path = parsed.path or ""
    if channel_type == CHANNEL_WEBHOOK:
        # 通用 WebHook 保留业务 query（含 SIEM 签名参数），只禁止 userinfo。
        public_url = urlunparse(
            ("https", parsed.netloc.lower(), path, "", urlencode(query_pairs), "")
        )
        return SanitizedChannelTarget(public_url=public_url.rstrip("?"), credential=None)

    extracted: str | None = (credential or "").strip() or None
    kept_pairs: list[tuple[str, str]] = []
    for key, value in query_pairs:
        if key.lower() in _SECRET_QUERY_KEYS:
            if extracted is None and value:
                extracted = value
            continue
        kept_pairs.append((key, value))

    public_path = path
    if channel_type == CHANNEL_SLACK:
        marker = "/services/"
        idx = path.lower().find(marker)
        if idx >= 0:
            secret_part = path[idx + len(marker) :].strip("/")
            if secret_part and extracted is None:
                extracted = secret_part
            public_path = path[: idx + len("/services")]
    elif channel_type in {CHANNEL_FEISHU, CHANNEL_LARK}:
        segments = [item for item in path.split("/") if item]
        if segments and extracted is None:
            extracted = segments[-1]
            public_path = "/" + "/".join(segments[:-1]) if len(segments) > 1 else "/"

    public_url = urlunparse(
        ("https", parsed.netloc.lower(), public_path, "", urlencode(kept_pairs), "")
    )
    if channel_type in IM_CHANNELS | GATEWAY_CHANNELS and not extracted:
        raise ChannelValidationError("CHANNEL_CREDENTIAL_REQUIRED")
    return SanitizedChannelTarget(public_url=public_url.rstrip("?"), credential=extracted)


def encrypt_channel_credential(value: str | None) -> str | None:
    if not value:
        return None
    return encrypt_field(value)


def decrypt_channel_credential(value: str | None) -> str | None:
    if not value:
        return None
    return decrypt_field(value)


def reconstruct_delivery_url(endpoint: WebhookEndpoint) -> str:
    """把已剥离的凭据装回官方 IM / 网关 URL。结果仅内存使用，禁止写入响应。"""

    channel_type = endpoint.channel_type or CHANNEL_WEBHOOK
    credential = decrypt_channel_credential(endpoint.credential_encrypted)
    parsed = urlparse(endpoint.url)
    if channel_type == CHANNEL_DINGTALK and credential:
        query = urlencode([("access_token", credential), *parse_qsl(parsed.query)])
        return urlunparse(("https", parsed.netloc, parsed.path, "", query, ""))
    if channel_type == CHANNEL_WECOM and credential:
        query = urlencode([("key", credential), *parse_qsl(parsed.query)])
        return urlunparse(("https", parsed.netloc, parsed.path, "", query, ""))
    if channel_type in {CHANNEL_FEISHU, CHANNEL_LARK, CHANNEL_SLACK} and credential:
        path = parsed.path.rstrip("/") + "/" + credential
        return urlunparse(("https", parsed.netloc, path, "", parsed.query, ""))
    return endpoint.url


def im_request_body(channel_type: str, *, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    """构造各 IM 官方机器人文本消息。payload 必须已脱敏。"""

    text = f"{event_type} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    if channel_type == CHANNEL_DINGTALK:
        return {"msgtype": "text", "text": {"content": text}}
    if channel_type in {CHANNEL_FEISHU, CHANNEL_LARK}:
        return {"msg_type": "text", "content": {"text": text}}
    if channel_type == CHANNEL_WECOM:
        return {"msgtype": "text", "text": {"content": text}}
    if channel_type == CHANNEL_SLACK:
        return {"text": text}
    return {"event_type": event_type, "payload": payload}


class ChannelNotificationSender(NotificationDeliverySender):
    """按 `channel_type` 分发：WebHook / IM / HTTPS 网关 / 站内信。"""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._session_factory = session_factory

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        channel_type = endpoint.channel_type or CHANNEL_WEBHOOK
        if channel_type == CHANNEL_INBOX:
            await self._deliver_inbox(delivery=delivery, payload=payload)
            return
        await self._deliver_http(endpoint=endpoint, delivery=delivery, payload=payload)

    async def _deliver_inbox(
        self, *, delivery: NotificationDelivery, payload: dict[str, object]
    ) -> None:
        recipient = (delivery.recipient_user_id or "").strip()
        if not recipient:
            raise RuntimeError(_INBOX_MISSING_RECIPIENT)
        if self._session_factory is None:
            raise RuntimeError(_INBOX_STORE_UNAVAILABLE)
        async with self._session_factory() as session:
            session.add(
                InAppMessage(
                    tenant_id=delivery.tenant_id,
                    recipient_user_id=recipient,
                    event_type=delivery.event_type,
                    title=delivery.event_type,
                    body_json=json.dumps(payload, sort_keys=True, default=str),
                    delivery_id=delivery.id,
                )
            )
            await session.commit()

    async def _deliver_http(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        channel_type = endpoint.channel_type or CHANNEL_WEBHOOK
        target = reconstruct_delivery_url(endpoint)
        headers = {
            "X-JanusGate-Event-Type": delivery.event_type,
            "X-JanusGate-Tenant-Id": delivery.tenant_id,
        }
        body: dict[str, object]
        if channel_type in IM_CHANNELS:
            body = im_request_body(channel_type, event_type=delivery.event_type, payload=payload)
        else:
            body = {
                "event_type": delivery.event_type,
                "delivery_id": delivery.id,
                "payload": payload,
            }
        if channel_type in GATEWAY_CHANNELS:
            secret = decrypt_channel_credential(endpoint.credential_encrypted)
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
        try:
            response = await self._client.post(target, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(_STABLE_TRANSPORT_ERROR) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(_STABLE_STATUS_ERROR.format(status=response.status_code))
