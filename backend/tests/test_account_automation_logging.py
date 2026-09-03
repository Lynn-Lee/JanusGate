"""#t73 结构化日志脱敏（关闭 P2#13）。"""

from __future__ import annotations

from app.core.logging import _redact_event_dict, redact_mapping


def test_redact_mapping_strips_nested_secrets() -> None:
    payload = {
        "account_id": 1,
        "password": "plain-secret",
        "nested": {"private_key": "-----BEGIN", "username": "deploy"},
        "items": [{"token": "abc", "ok": True}],
    }
    redacted = redact_mapping(payload)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["private_key"] == "[REDACTED]"
    assert redacted["nested"]["username"] == "deploy"
    assert redacted["items"][0]["token"] == "[REDACTED]"
    assert redacted["items"][0]["ok"] is True
    assert redacted["account_id"] == 1


def test_log_processor_redacts_event_dict() -> None:
    event = _redact_event_dict(
        None,
        "info",
        {
            "event": "change_secret",
            "username": "deploy",
            "new_password": "N3w!Rotation-Pass",
            "secret_id": "sec_deploy",
        },
    )
    assert event["event"] == "change_secret"
    assert event["username"] == "deploy"
    assert event["new_password"] == "[REDACTED]"
    assert event["secret_id"] == "[REDACTED]"
