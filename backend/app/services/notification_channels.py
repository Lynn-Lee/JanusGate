"""通知渠道目标校验、凭据剥离与脱敏 payload。

IM 只允许官方 host，机器人 token 从 URL 剥离后加密落库；通用 WebHook 保留
query（只禁止 userinfo），避免误伤 SIEM 回调地址。邮件/短信本切片走 HTTPS
网关 Bearer，不直连 SMTP。失败码稳定且不含凭据。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

CHANNEL_WEBHOOK = "webhook"
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_FEISHU = "feishu"
CHANNEL_LARK = "lark"
CHANNEL_WECOM = "wecom"
CHANNEL_SLACK = "slack"
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_INBOX = "inbox"

CHANNEL_TYPES = frozenset(
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
IM_CHANNEL_TYPES = frozenset(
    {CHANNEL_DINGTALK, CHANNEL_FEISHU, CHANNEL_LARK, CHANNEL_WECOM, CHANNEL_SLACK}
)
GATEWAY_CHANNEL_TYPES = frozenset({CHANNEL_SMS, CHANNEL_EMAIL})
OUTBOUND_HTTP_CHANNEL_TYPES = frozenset({CHANNEL_WEBHOOK}) | IM_CHANNEL_TYPES | GATEWAY_CHANNEL_TYPES

OFFICIAL_HOSTS: dict[str, frozenset[str]] = {
    CHANNEL_DINGTALK: frozenset({"oapi.dingtalk.com"}),
    CHANNEL_FEISHU: frozenset({"open.feishu.cn"}),
    CHANNEL_LARK: frozenset({"open.larksuite.com"}),
    CHANNEL_WECOM: frozenset({"qyapi.weixin.qq.com"}),
    CHANNEL_SLACK: frozenset({"hooks.slack.com"}),
}

_QUERY_SECRET_KEYS = frozenset({"access_token", "key", "token", "secret", "password"})
_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\\b(token|password|passwd|secret|credential)\\s*=\\s*[^\\s,;]+"
)


class ChannelTargetError(ValueError):
    """渠道 URL/凭据不合法。``args[0]`` 为稳定业务错误码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SanitizedChannelTarget:
    """入库用的已剥离凭据目标。``credential`` 仍是明文，调用方负责加密。"""

    channel_type: str
    url: str
    credential: str | None


def sanitize_channel_target(
    *,
    channel_type: str,
    url: str,
    credential: str | None = None,
) -> SanitizedChannelTarget:
    """校验渠道类型与 URL，并把 IM token 从 query/路径剥离。

    失败时抛出 ``ChannelTargetError``，错误码可直接作为 HTTP 400 detail。
    """
    normalized_type = channel_type.strip().lower()
    if normalized_type not in CHANNEL_TYPES:
        raise ChannelTargetError("INVALID_CHANNEL_TYPE")

    if normalized_type == CHANNEL_INBOX:
        return SanitizedChannelTarget(channel_type=CHANNEL_INBOX, url="", credential=None)

    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ChannelTargetError("INVALID_WEBHOOK_URL")
    if parsed.username or parsed.password:
        raise ChannelTargetError("INVALID_WEBHOOK_URL")
    if not parsed.hostname:
        raise ChannelTargetError("INVALID_WEBHOOK_URL")

    hostname = parsed.hostname.lower()
    explicit_credential = (credential or "").strip() or None

    if normalized_type in IM_CHANNEL_TYPES:
        allowed = OFFICIAL_HOSTS[normalized_type]
        if hostname not in allowed:
            raise ChannelTargetError("INVALID_CHANNEL_HOST")
        stored_url, extracted = _strip_im_secrets(normalized_type, parsed)
        final_credential = explicit_credential or extracted
        if not final_credential:
            raise ChannelTargetError("INVALID_CHANNEL_CREDENTIAL")
        return SanitizedChannelTarget(
            channel_type=normalized_type,
            url=stored_url,
            credential=final_credential,
        )

    if normalized_type in GATEWAY_CHANNEL_TYPES:
        if not explicit_credential:
            raise ChannelTargetError("INVALID_CHANNEL_CREDENTIAL")
        stored = urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        return SanitizedChannelTarget(
            channel_type=normalized_type,
            url=stored.rstrip("/") if parsed.path in {"", "/"} else stored,
            credential=explicit_credential,
        )

    stored = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )
    return SanitizedChannelTarget(
        channel_type=CHANNEL_WEBHOOK,
        url=stored,
        credential=explicit_credential,
    )


def build_delivery_url(*, channel_type: str, stored_url: str, credential: str | None) -> str:
    """用已剥离凭据重建投递 URL。调用方不得把返回值写入日志或 last_error。"""
    if channel_type == CHANNEL_DINGTALK:
        return f"{stored_url}?access_token={credential or ''}"
    if channel_type == CHANNEL_WECOM:
        return f"{stored_url}?key={credential or ''}"
    if channel_type in {CHANNEL_FEISHU, CHANNEL_LARK, CHANNEL_SLACK}:
        base = stored_url.rstrip("/")
        return f"{base}/{credential or ''}"
    return stored_url


def format_channel_text(*, event_type: str, payload: dict[str, object]) -> str:
    """把已脱敏 payload 压成 IM 文本，避免渠道协议差异泄漏结构字段。"""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"{event_type}\n{body}"


def channel_request_body(
    *,
    channel_type: str,
    event_type: str,
    delivery_id: int | None,
    payload: dict[str, object],
) -> dict[str, object]:
    """构造各渠道请求体。只使用已脱敏 payload。"""
    text = format_channel_text(event_type=event_type, payload=payload)
    if channel_type == CHANNEL_DINGTALK:
        return {"msgtype": "text", "text": {"content": text}}
    if channel_type in {CHANNEL_FEISHU, CHANNEL_LARK}:
        return {"msg_type": "text", "content": {"text": text}}
    if channel_type == CHANNEL_WECOM:
        return {"msgtype": "text", "text": {"content": text}}
    if channel_type == CHANNEL_SLACK:
        return {"text": text}
    return {"event_type": event_type, "delivery_id": delivery_id, "payload": payload}


def redact_notification_payload(value: Any) -> Any:
    """递归脱敏 token/password/secret/credential，供入队与站内信共用。"""
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
        return _SENSITIVE_ASSIGNMENT.sub(r"\\1=[REDACTED]", value)
    return value


def parse_event_types(value: str) -> list[str]:
    """解析 JSON 事件类型列表；非法结构视为空列表。"""
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _strip_im_secrets(channel_type: str, parsed: Any) -> tuple[str, str | None]:
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _QUERY_SECRET_KEYS
    ]
    extracted: str | None = None
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in _QUERY_SECRET_KEYS and value:
            extracted = value
            break

    path = parsed.path or "/"
    if channel_type in {CHANNEL_FEISHU, CHANNEL_LARK}:
        path, extracted = _split_trailing_secret(path, extracted, prefix="/open-apis/bot/v2/hook")
    elif channel_type == CHANNEL_SLACK:
        path, extracted = _split_trailing_secret(path, extracted, prefix="/services")
    stored = urlunparse(
        (
            parsed.scheme,
            parsed.netloc.split("@")[-1],
            path,
            "",
            urlencode(query_pairs),
            "",
        )
    )
    return stored.rstrip("?"), extracted


def _split_trailing_secret(
    path: str, extracted: str | None, *, prefix: str
) -> tuple[str, str | None]:
    normalized = path.rstrip("/")
    prefix_normalized = prefix.rstrip("/")
    if normalized.startswith(prefix_normalized) and len(normalized) > len(prefix_normalized):
        remainder = normalized[len(prefix_normalized) :].lstrip("/")
        if remainder:
            return prefix_normalized, extracted or remainder
    return prefix_normalized if normalized.startswith(prefix_normalized) else normalized, extracted
