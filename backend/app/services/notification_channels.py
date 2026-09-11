"""通知渠道类型、官方 host 白名单与 URL 校验。

失败模式：非 HTTPS 的 HTTP 渠道、把机器人 token 放进 query、以及非官方 IM host
一律 fail-closed，避免凭据进日志或被钓鱼域名收走。
"""
from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException


class NotificationChannelType(StrEnum):
    WEBHOOK = "webhook"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"
    LARK = "lark"
    WECOM = "wecom"
    SLACK = "slack"
    SMS = "sms"
    EMAIL = "email"
    INBOX = "inbox"


IM_CHANNEL_HOSTS: dict[NotificationChannelType, frozenset[str]] = {
    NotificationChannelType.DINGTALK: frozenset({"oapi.dingtalk.com"}),
    NotificationChannelType.FEISHU: frozenset({"open.feishu.cn"}),
    NotificationChannelType.LARK: frozenset({"open.larksuite.com"}),
    NotificationChannelType.WECOM: frozenset({"qyapi.weixin.qq.com"}),
    NotificationChannelType.SLACK: frozenset({"hooks.slack.com", "slack.com"}),
}

HTTPS_CHANNELS = frozenset(
    {
        NotificationChannelType.WEBHOOK,
        NotificationChannelType.DINGTALK,
        NotificationChannelType.FEISHU,
        NotificationChannelType.LARK,
        NotificationChannelType.WECOM,
        NotificationChannelType.SLACK,
        NotificationChannelType.SMS,
    }
)


def sanitize_channel_url(url: str) -> str:
    """响应里去掉 query/fragment，避免机器人 token 被列表接口回显。"""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def validate_channel_url(*, channel_type: NotificationChannelType, url: str) -> str:
    """校验渠道 URL；返回规范化后的原始 URL（保留 path，拒绝 query 中的凭据）。

    Raises:
        HTTPException: 协议、host 或凭据位置不合法。
    """
    stripped = url.strip()
    if channel_type is NotificationChannelType.INBOX:
        if stripped != "inbox://local":
            raise HTTPException(status_code=400, detail="INVALID_CHANNEL_URL")
        return stripped

    parts = urlsplit(stripped)
    if parts.query or parts.fragment:
        raise HTTPException(status_code=400, detail="CHANNEL_CREDENTIAL_IN_URL")

    if channel_type is NotificationChannelType.EMAIL:
        if parts.scheme not in {"smtp", "smtps"} or not parts.hostname:
            raise HTTPException(status_code=400, detail="INVALID_CHANNEL_URL")
        if parts.scheme == "smtp" and (parts.port or 25) == 25:
            raise HTTPException(status_code=400, detail="EMAIL_STARTTLS_REQUIRED")
        if parts.username or parts.password:
            raise HTTPException(status_code=400, detail="CHANNEL_CREDENTIAL_IN_URL")
        return stripped

    if channel_type in HTTPS_CHANNELS:
        if parts.scheme != "https" or not parts.hostname:
            raise HTTPException(status_code=400, detail="INVALID_WEBHOOK_URL")
        allowed_hosts = IM_CHANNEL_HOSTS.get(channel_type)
        if allowed_hosts is not None and parts.hostname.lower() not in allowed_hosts:
            raise HTTPException(status_code=400, detail="CHANNEL_HOST_NOT_ALLOWED")
        return stripped

    raise HTTPException(status_code=400, detail="INVALID_CHANNEL_TYPE")
