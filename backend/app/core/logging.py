"""结构化日志封装（#t73）。

统一使用 ``structlog``，并在事件字典中递归剥离敏感键，关闭 P2#13（print / 明文密码进日志）。
凭据、私钥、token 等字段无论嵌套多深都替换为 ``[REDACTED]``，避免执行器或 worker 误记。
"""

from __future__ import annotations

from typing import Any

import structlog

_SENSITIVE = frozenset(
    {
        "access_token",
        "authorization",
        "connection_string",
        "cookie",
        "credential",
        "credential_value",
        "database_url",
        "dsn",
        "new_password",
        "new_secret_id",
        "old_password",
        "passphrase",
        "password",
        "plain_password",
        "plain_secret",
        "plaintext",
        "previous_secret_id",
        "private_key",
        "refresh_token",
        "secret",
        "secret_id",
        "signing_secret",
        "token",
    }
)

_configured = False


def configure_logging(*, json_logs: bool = False) -> None:
    """幂等配置 structlog 处理器链。

    :param json_logs: 为 True 时输出 JSON 行，便于集中采集；测试默认 False。
    """

    global _configured
    if _configured:
        return
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_event_dict,
        structlog.processors.StackInfoRenderer(),
    ]
    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """返回已配置脱敏的绑定 logger。

    :param name: 可选模块名，写入 ``logger`` 字段便于检索。
    """

    configure_logging()
    logger = structlog.get_logger()
    if name:
        return logger.bind(logger=name)
    return logger


def redact_mapping(payload: Any) -> Any:
    """递归脱敏映射/列表；供测试与队列旁路复用。"""

    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if str(key).lower() in _SENSITIVE:
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_mapping(value)
        return redacted
    if isinstance(payload, list):
        return [redact_mapping(item) for item in payload]
    return payload


def _redact_event_dict(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    del logger, method_name
    redacted = redact_mapping(event_dict)
    if not isinstance(redacted, dict):
        return event_dict
    return redacted
