"""Shared notification payload redaction helpers (#t47 / #t75)."""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "password", "secret", "token")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|credential)\s*=\s*[^\s,;]+"
)


def redact_payload(value: Any) -> Any:
    """Recursively redact sensitive keys and assignment-style secrets in strings."""

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
