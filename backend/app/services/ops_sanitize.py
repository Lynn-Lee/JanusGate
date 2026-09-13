"""#t77 作业中心输入收敛：路径、变量、cron、临时命令。

失败一律抛出稳定错误码。拒绝敏感键、Jinja 与凭据进 extra vars，避免 pickle 之外的
第二条注入面（模板求值 / argv 拼命令）。
"""
from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.automation_worker import SENSITIVE_PAYLOAD_KEYS

ADHOC_PLAYBOOK = "janusgate-adhoc.yml"
ADHOC_MODULES = frozenset({"shell", "command"})
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_JINJA_MARKERS = ("{{", "{%", "{#")
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


class OpsConfigError(ValueError):
    """作业中心配置不合法。对外映射为稳定错误码。"""


def playbook_relative_path(value: str) -> str:
    """校验 Playbook 相对路径：必须是 playbook root 内的 ``.yml/.yaml``。"""
    raw = value.strip()
    candidate = Path(raw)
    if (
        not raw
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.suffix not in {".yml", ".yaml"}
    ):
        raise OpsConfigError("OPS_PLAYBOOK_PATH_INVALID")
    return str(candidate)


def adhoc_module(value: str | None) -> str:
    module = (value or "").strip().lower()
    if module not in ADHOC_MODULES:
        raise OpsConfigError("OPS_ADHOC_MODULE_INVALID")
    return module


def adhoc_command(value: str | None) -> str:
    command = (value or "").strip()
    if not command or len(command) > 2000:
        raise OpsConfigError("OPS_ADHOC_COMMAND_INVALID")
    _reject_jinja(command)
    if "\x00" in command:
        raise OpsConfigError("OPS_ADHOC_COMMAND_INVALID")
    return command


def extra_vars(value: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    """校验 extra vars：仅标量、无敏感键、无 Jinja、无 janusgate_ 前缀。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise OpsConfigError("OPS_EXTRA_VARS_INVALID")
    cleaned: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _VAR_NAME.match(key):
            raise OpsConfigError("OPS_EXTRA_VARS_INVALID")
        if key.lower() in SENSITIVE_PAYLOAD_KEYS or key.lower().startswith("janusgate_"):
            raise OpsConfigError("OPS_EXTRA_VARS_SECRET")
        if isinstance(item, bool):
            cleaned[key] = item
            continue
        if isinstance(item, (int, float)):
            cleaned[key] = item
            continue
        if not isinstance(item, str):
            raise OpsConfigError("OPS_EXTRA_VARS_INVALID")
        _reject_jinja(item)
        cleaned[key] = item
    return cleaned


def extra_vars_json(value: dict[str, Any] | None) -> str:
    return json.dumps(extra_vars(value), sort_keys=True, separators=(",", ":"))


def parse_asset_ids(value: list[int]) -> list[int]:
    if not value:
        raise OpsConfigError("OPS_TARGETS_REQUIRED")
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise OpsConfigError("OPS_TARGETS_INVALID")
        ids.append(item)
    return ids


def cron_expr(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    expr = value.strip()
    parts = expr.split()
    if len(parts) != 5:
        raise OpsConfigError("OPS_CRON_INVALID")
    for index, part in enumerate(parts):
        _validate_cron_field(part, *_CRON_RANGES[index])
    return expr


def next_cron_run(expr: str, *, after: datetime) -> datetime:
    """从 ``after`` 的下一分钟起，最多向前搜一年。找不到则失败。"""
    cursor = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cursor + timedelta(days=366)
    parts = expr.split()
    while cursor <= limit:
        if (
            _cron_field_matches(parts[0], cursor.minute, 0, 59)
            and _cron_field_matches(parts[1], cursor.hour, 0, 23)
            and _cron_field_matches(parts[2], cursor.day, 1, monthrange(cursor.year, cursor.month)[1])
            and _cron_field_matches(parts[3], cursor.month, 1, 12)
            and _cron_field_matches(parts[4], cursor.isoweekday() % 7, 0, 6)
        ):
            return cursor
        cursor += timedelta(minutes=1)
    raise OpsConfigError("OPS_CRON_UNSATISFIABLE")


def loads_ids(raw: str) -> list[int]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        return []
    return [int(item) for item in parsed if isinstance(item, int) and not isinstance(item, bool)]


def loads_vars(raw: str) -> dict[str, str | int | float | bool]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return {}
    return extra_vars(parsed)


def _reject_jinja(value: str) -> None:
    if any(marker in value for marker in _JINJA_MARKERS):
        raise OpsConfigError("OPS_JINJA_NOT_ALLOWED")


def _validate_cron_field(expr: str, lo: int, hi: int) -> None:
    for part in expr.split(","):
        chunk = part.strip()
        if chunk == "*":
            continue
        if chunk.startswith("*/"):
            step = _int_token(chunk[2:], lo, hi)
            if step <= 0:
                raise OpsConfigError("OPS_CRON_INVALID")
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            start, end = _int_token(start_s, lo, hi), _int_token(end_s, lo, hi)
            if start > end:
                raise OpsConfigError("OPS_CRON_INVALID")
            continue
        _int_token(chunk, lo, hi)


def _cron_field_matches(expr: str, value: int, lo: int, hi: int) -> bool:
    for part in expr.split(","):
        chunk = part.strip()
        if chunk == "*":
            return True
        if chunk.startswith("*/"):
            step = int(chunk[2:])
            if (value - lo) % step == 0 and lo <= value <= hi:
                return True
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            if int(start_s) <= value <= int(end_s):
                return True
            continue
        if int(chunk) == value:
            return True
    return False


def _int_token(raw: str, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise OpsConfigError("OPS_CRON_INVALID") from exc
    if value < lo or value > hi:
        raise OpsConfigError("OPS_CRON_INVALID")
    return value
