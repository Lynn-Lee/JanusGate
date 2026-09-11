"""通知 payload 脱敏：入库与投递共用，避免 IM/邮件/短信泄露凭据。"""
from __future__ import annotations

import json
import re
from typing import Any

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|credential)\s*=\s*[^\s,;]+"
)


def redact_payload(value: Any) -> Any:
    """递归打码敏感键名与赋值片段；列表/字典结构保持不变。"""
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


def payload_dict(value: str) -> dict[str, object]:
    """把投递记录中的 JSON 文本解析为 dict；非法结构返回空对象，避免 worker 崩溃。"""
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): item for key, item in parsed.items()}


def event_types(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def config_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): item for key, item in parsed.items()}
