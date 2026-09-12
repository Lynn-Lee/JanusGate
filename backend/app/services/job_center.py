"""#t77 作业中心领域规则：参数脱敏、cron、runas 校验与 JSON-only 入队。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.job_center import OpsJob, OpsJobVariable, OpsPlaybook
from app.services.automation_worker import (
    ALLOWED_JOB_TYPES,
    SENSITIVE_PAYLOAD_KEYS,
    AutomationJobQueue,
    JsonValue,
    _assert_no_sensitive_payload_keys,
)

JOB_KIND_PLAYBOOK = "playbook"
JOB_KIND_ADHOC = "adhoc"
ADHOC_MODULES: frozenset[str] = frozenset({"shell", "command"})
QUEUE_JOB_TYPES: dict[str, str] = {
    JOB_KIND_PLAYBOOK: "job.playbook",
    JOB_KIND_ADHOC: "job.adhoc",
}
VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PLAYBOOK_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(yml|yaml)$")
RESERVED_VAR_PREFIX = "janusgate_"
JINJA_MARKERS = ("{{", "}}", "{%", "%}")


def dump_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_int_list(raw: str) -> list[int]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("TARGET_ASSETS_REQUIRED")
    asset_ids: list[int] = []
    for item in parsed:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ValueError("TARGET_ASSETS_INVALID")
        asset_ids.append(item)
    return asset_ids


def load_extra_vars(raw: str) -> dict[str, JsonValue]:
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("EXTRA_VARS_INVALID")
    return sanitize_extra_vars(parsed)


def sanitize_extra_vars(values: Mapping[str, Any]) -> dict[str, JsonValue]:
    """拒绝敏感键、Jinja 注入与非 JSON 标量，避免参数化回退成 pickle/任意对象。"""

    sanitized: dict[str, JsonValue] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not VARIABLE_NAME_RE.fullmatch(key):
            raise ValueError("EXTRA_VARS_KEY_INVALID")
        if key.lower().startswith(RESERVED_VAR_PREFIX):
            raise ValueError("EXTRA_VARS_KEY_RESERVED")
        if key.lower() in SENSITIVE_PAYLOAD_KEYS:
            raise ValueError("AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET")
        sanitized[key] = _sanitize_json_scalar(value)
    _assert_no_sensitive_payload_keys(sanitized)
    return sanitized


def sanitize_variable_name(name: str) -> str:
    cleaned = name.strip()
    if not VARIABLE_NAME_RE.fullmatch(cleaned):
        raise ValueError("VARIABLE_NAME_INVALID")
    if cleaned.lower().startswith(RESERVED_VAR_PREFIX) or cleaned.lower() in SENSITIVE_PAYLOAD_KEYS:
        raise ValueError("VARIABLE_NAME_FORBIDDEN")
    return cleaned


def sanitize_variable_value(value: str) -> str:
    text = value.strip()
    if not text or len(text) > 2000:
        raise ValueError("VARIABLE_VALUE_INVALID")
    _reject_jinja(text)
    _reject_secret_material(text)
    return text


def sanitize_adhoc_command(command: str) -> str:
    text = command.strip()
    if not text or len(text) > 4000:
        raise ValueError("ADHOC_COMMAND_INVALID")
    _reject_jinja(text)
    _reject_secret_material(text)
    return text


def sanitize_playbook_filename(filename: str) -> str:
    cleaned = filename.strip()
    if not PLAYBOOK_FILENAME_RE.fullmatch(cleaned) or "/" in cleaned or "\\" in cleaned:
        raise ValueError("PLAYBOOK_FILENAME_INVALID")
    return cleaned


def sanitize_cron_expr(expr: str | None) -> str | None:
    if expr is None:
        return None
    cleaned = expr.strip()
    if cleaned == "":
        return None
    _cron_fields(cleaned)
    return cleaned


def sanitize_timezone(name: str) -> str:
    cleaned = name.strip() or "UTC"
    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("TIMEZONE_INVALID") from exc
    return cleaned


def next_run_at(*, cron_expr: str, timezone: str, after: datetime) -> datetime:
    tz = ZoneInfo(timezone)
    local_after = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    minute, hour, dom, month, dow = _cron_fields(cron_expr)
    cursor = local_after
    limit = local_after + timedelta(days=366)
    while cursor <= limit:
        if (
            cursor.minute in minute
            and cursor.hour in hour
            and cursor.day in dom
            and cursor.month in month
            and cursor.weekday() in dow
        ):
            return cursor.astimezone(UTC)
        cursor += timedelta(minutes=1)
    raise ValueError("CRON_NO_NEXT_RUN")


def queue_job_type(job_kind: str) -> str:
    job_type = QUEUE_JOB_TYPES.get(job_kind)
    if job_type is None or job_type not in ALLOWED_JOB_TYPES:
        raise ValueError("UNSUPPORTED_JOB_KIND")
    return job_type


async def enqueue_ops_job(
    *,
    queue: AutomationJobQueue,
    job: OpsJob,
    requested_by: str,
) -> str:
    """队列只携带 job_id。变量、命令与凭据均不入队，关闭 P0#10 pickle 回退面。"""

    return await queue.enqueue(
        tenant_id=job.tenant_id,
        requested_by=requested_by,
        job_type=queue_job_type(job.job_kind),
        payload={"job_id": job.id},
    )


async def merge_job_extra_vars(session: AsyncSession, job: OpsJob) -> dict[str, JsonValue]:
    merged = load_extra_vars(job.extra_vars_json)
    result = await session.execute(
        select(OpsJobVariable)
        .where(OpsJobVariable.job_id == job.id)
        .where(OpsJobVariable.tenant_id == job.tenant_id)
        .order_by(OpsJobVariable.id.asc())
    )
    for variable in result.scalars().all():
        merged[variable.name] = variable.value
    return sanitize_extra_vars(merged)


async def get_scoped_playbook(
    session: AsyncSession, *, tenant_id: str, playbook_id: int
) -> OpsPlaybook:
    result = await session.execute(
        select(OpsPlaybook)
        .where(OpsPlaybook.id == playbook_id)
        .where(OpsPlaybook.tenant_id == tenant_id)
    )
    playbook = result.scalar_one_or_none()
    if playbook is None or not playbook.is_active:
        raise ValueError("PLAYBOOK_NOT_FOUND")
    return playbook


async def get_runas_account(
    session: AsyncSession, *, tenant_id: str, account_id: int
) -> Account:
    result = await session.execute(
        select(Account).where(Account.id == account_id).where(Account.tenant_id == tenant_id)
    )
    account = result.scalar_one_or_none()
    if account is None or account.status != "active":
        raise ValueError("RUNAS_ACCOUNT_UNAVAILABLE")
    if account.protocol.lower() != "ssh":
        raise ValueError("RUNAS_ACCOUNT_PROTOCOL_INVALID")
    return account


async def get_active_assets(
    session: AsyncSession, *, tenant_id: str, asset_ids: list[int]
) -> list[Asset]:
    result = await session.execute(
        select(Asset)
        .where(Asset.id.in_(asset_ids))
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
    )
    assets = list(result.scalars().all())
    by_id = {asset.id: asset for asset in assets}
    if any(asset_id not in by_id for asset_id in asset_ids):
        raise ValueError("ASSET_NOT_FOUND")
    return [by_id[asset_id] for asset_id in asset_ids]


def adhoc_inline_playbook() -> str:
    return (
        "- hosts: all\n"
        "  gather_facts: false\n"
        "  become: false\n"
        "  tasks:\n"
        "    - name: janusgate-adhoc-shell\n"
        "      ansible.builtin.shell: \"{{ janusgate_adhoc_command }}\"\n"
        "      when: janusgate_adhoc_module == \"shell\"\n"
        "    - name: janusgate-adhoc-command\n"
        "      ansible.builtin.command: \"{{ janusgate_adhoc_command }}\"\n"
        "      when: janusgate_adhoc_module == \"command\"\n"
    )


def _sanitize_json_scalar(value: Any) -> JsonValue:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    if isinstance(value, str):
        if len(value) > 2000:
            raise ValueError("EXTRA_VARS_VALUE_INVALID")
        _reject_jinja(value)
        _reject_secret_material(value)
        return value
    raise ValueError("EXTRA_VARS_VALUE_INVALID")


def _reject_jinja(text: str) -> None:
    if any(marker in text for marker in JINJA_MARKERS):
        raise ValueError("JINJA_TEMPLATE_FORBIDDEN")


def _reject_secret_material(text: str) -> None:
    lowered = text.lower()
    if "begin " in lowered and "private" in lowered:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET")
    if "password=" in lowered or "token=" in lowered or "secret=" in lowered:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET")


def _cron_fields(expr: str) -> tuple[frozenset[int], frozenset[int], frozenset[int], frozenset[int], frozenset[int]]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("CRON_INVALID")
    minute = _cron_field(parts[0], 0, 59)
    hour = _cron_field(parts[1], 0, 23)
    dom = _cron_field(parts[2], 1, 31)
    month = _cron_field(parts[3], 1, 12)
    # cron DOW 0=Sunday；datetime.weekday() 0=Monday。统一存成 weekday()。
    raw_dow = _cron_field(parts[4], 0, 7)
    converted: set[int] = set()
    for item in raw_dow:
        if item in {0, 7}:
            converted.add(6)
        else:
            converted.add(item - 1)
    return minute, hour, dom, month, frozenset(converted)


def _cron_field(field: str, minimum: int, maximum: int) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        base = part
        if "/" in part:
            base, step_raw = part.split("/", 1)
            if not step_raw.isdigit() or int(step_raw) <= 0:
                raise ValueError("CRON_INVALID")
            step = int(step_raw)
        if base in {"*", ""}:
            start, end = minimum, maximum
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            if not start_raw.isdigit() or not end_raw.isdigit():
                raise ValueError("CRON_INVALID")
            start, end = int(start_raw), int(end_raw)
        else:
            if not base.isdigit():
                raise ValueError("CRON_INVALID")
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError("CRON_INVALID")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("CRON_INVALID")
    return frozenset(values)
