"""通知 payload 脱敏。投递队列、扇出与站内信共用，避免把凭据写进下游。"""
from __future__ import annotations

import json
import re
from typing import Any

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|credential)\s*=\s*[^\s,;]+"
)


def redact_payload(value: Any) -> Any:
    """递归打码敏感键与 `token=` 赋值片段；结构保持不变。"""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
    return value


def payload_json(value: Any) -> str:
    """把已脱敏 payload 序列化入库。"""
    return json.dumps(redact_payload(value), sort_keys=True, default=str)


def payload_summary(payload: dict[str, object]) -> str:
    """给 IM / 邮件 / 短信 / 站内信用的短文本，不含敏感键明文。"""
    event = str(payload.get("summary") or payload.get("message") or "")
    if event:
        return str(redact_payload(event))
    keys = ", ".join(sorted(str(key) for key in payload)[:8])
    return f"notification keys={keys}" if keys else "notification"
