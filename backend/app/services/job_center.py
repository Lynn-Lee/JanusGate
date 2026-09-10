"""#t77 作业中心：作业定义、参数化、runas、周期调度（JSON-only，无 pickle）。"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AutomationJobRun, JobDefinition
from app.services.ansible_playbook import AnsiblePlaybookWorkerHandler
from app.services.automation_worker import (
    ALLOWED_JOB_TYPES,
    AutomationJobQueue,
    JsonValue,
    _assert_no_sensitive_payload_keys,
)

ADHOC_PLAYBOOK_NAME = "adhoc-command.yml"
COMMAND_JOB_TYPES = frozenset({"adhoc.command", "batch.command"})
MAX_COMMAND_LENGTH = 500
MAX_EXTRA_VARIABLES = 40


class AdhocCommandWorkerHandler:
    """把临时/批量命令转成白名单 playbook，复用 Ansible runner，不走 pickle。"""

    def __init__(self, playbook_handler: AnsiblePlaybookWorkerHandler) -> None:
        self._playbook_handler = playbook_handler

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        await self._playbook_handler(
            tenant_id=tenant_id,
            requested_by=requested_by,
            payload=command_payload_to_playbook(payload),
            message_id=message_id,
        )


def command_payload_to_playbook(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """将 adhoc/batch 载荷收敛为 ansible.playbook 契约。"""

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("ADHOC_COMMAND_TOO_LONG")
    target_asset_ids = payload.get("target_asset_ids")
    extra = payload.get("extra_variables")
    extra_variables: dict[str, JsonValue] = dict(extra) if isinstance(extra, dict) else {}
    extra_variables["janusgate_adhoc_command"] = command.strip()
    recorded = payload.get("recorded_job_type")
    return {
        "playbook_name": ADHOC_PLAYBOOK_NAME,
        "target_asset_ids": target_asset_ids if isinstance(target_asset_ids, list) else [],
        "check_mode": False,
        "extra_variables": extra_variables,
        "job_definition_id": payload.get("job_definition_id"),
        "run_as_user_id": payload.get("run_as_user_id"),
        "recorded_job_type": recorded if isinstance(recorded, str) else "adhoc.command",
    }


def new_job_id() -> str:
    return f"job_{uuid4().hex}"


def validate_job_definition_payload(*, job_type: str, payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    """校验作业类型白名单与载荷形状，并拒绝敏感键。"""

    if job_type not in ALLOWED_JOB_TYPES:
        raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")
    _assert_no_sensitive_payload_keys(payload)
    if job_type == "ansible.playbook":
        return _require_playbook_payload(payload)
    if job_type in COMMAND_JOB_TYPES:
        return _require_command_payload(payload)
    if job_type == "asset.scan":
        return _require_keys(payload, required={"asset_id", "scan_profile"})
    if job_type in {"credential.rotate", "account.verify"}:
        return _require_keys(payload, required={"account_id"})
    raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")


def validate_extra_variables(extra_variables: Mapping[str, Any] | None) -> dict[str, JsonValue]:
    """参数化变量必须是浅 JSON 对象，且不得携带敏感键。"""

    if extra_variables is None:
        return {}
    if not isinstance(extra_variables, dict):
        raise ValueError("JOB_EXTRA_VARIABLES_INVALID")
    if len(extra_variables) > MAX_EXTRA_VARIABLES:
        raise ValueError("JOB_EXTRA_VARIABLES_TOO_MANY")
    _assert_no_sensitive_payload_keys(extra_variables)
    return dict(extra_variables)


def resolve_run_as_user_id(*, actor: dict[str, Any], requested_run_as: str | None) -> str | None:
    """执行身份策略：普通人只能 runas 自己；admin 可指定他人。"""

    actor_id = str(actor.get("id") or "")
    if not requested_run_as:
        return None
    if requested_run_as == actor_id:
        return requested_run_as
    permissions = actor.get("permissions") or []
    if "admin" in permissions:
        return requested_run_as
    raise ValueError("JOB_RUNAS_FORBIDDEN")


def build_enqueue_payload(
    definition: JobDefinition, *, extra_override: Mapping[str, Any] | None = None
) -> dict[str, JsonValue]:
    """合并作业模板与一次运行的额外变量，写入队列载荷。"""

    extra = dict(definition.extra_variables or {})
    extra.update(validate_extra_variables(extra_override))
    payload = dict(definition.payload or {})
    payload["extra_variables"] = extra
    payload["job_definition_id"] = definition.id
    if definition.run_as_user_id:
        payload["run_as_user_id"] = definition.run_as_user_id
    payload["recorded_job_type"] = definition.job_type
    if definition.job_type in COMMAND_JOB_TYPES:
        return command_payload_to_playbook(payload)
    return payload


def queue_job_type(job_type: str) -> str:
    """临时/批量命令在队列里仍走 ansible.playbook handler 契约。"""

    if job_type in COMMAND_JOB_TYPES:
        return "ansible.playbook"
    return job_type


def parse_cron_expression(expression: str) -> tuple[str, str, str, str, str]:
    """解析五段 cron（分 时 日 月 周），失败抛 ``INVALID_CRON``。"""

    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError("INVALID_CRON")
    minute, hour, day, month, weekday = parts
    _assert_cron_field(minute, 0, 59)
    _assert_cron_field(hour, 0, 23)
    _assert_cron_field(day, 1, 31)
    _assert_cron_field(month, 1, 12)
    _assert_cron_field(weekday, 0, 6)
    return minute, hour, day, month, weekday


def next_cron_run(expression: str, *, after: datetime) -> datetime:
    """从 ``after`` 的下一分钟起寻找下一次命中，最多向前看 366 天。"""

    fields = parse_cron_expression(expression)
    cursor = (after.astimezone(UTC) + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = cursor + timedelta(days=366)
    while cursor <= limit:
        if _cron_matches(fields, cursor):
            return cursor
        cursor += timedelta(minutes=1)
    raise ValueError("CRON_NEXT_RUN_NOT_FOUND")


def _cron_matches(fields: tuple[str, str, str, str, str], moment: datetime) -> bool:
    minute, hour, day, month, weekday = fields
    sunday_based = (moment.weekday() + 1) % 7
    return (
        _cron_field_matches(minute, moment.minute)
        and _cron_field_matches(hour, moment.hour)
        and _cron_field_matches(day, moment.day)
        and _cron_field_matches(month, moment.month)
        and _cron_field_matches(weekday, sunday_based)
    )


def _cron_field_matches(expr: str, value: int) -> bool:
    for part in expr.split(","):
        token = part.strip()
        if not token:
            continue
        if token == "*":
            return True
        if "/" in token:
            base, step_s = token.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError("INVALID_CRON")
            if base == "*" and value % step == 0:
                return True
            if "-" in base:
                start, end = (int(item) for item in base.split("-", 1))
                if start <= value <= end and (value - start) % step == 0:
                    return True
            continue
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            if start <= value <= end:
                return True
            continue
        if int(token) == value:
            return True
    return False


def _assert_cron_field(expr: str, minimum: int, maximum: int) -> None:
    for part in expr.split(","):
        token = part.strip()
        if token in {"", "*"}:
            continue
        if "/" in token:
            base, step_s = token.split("/", 1)
            int(step_s)
            if base == "*":
                continue
            token = base
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            if start < minimum or end > maximum or start > end:
                raise ValueError("INVALID_CRON")
            continue
        number = int(token)
        if number < minimum or number > maximum:
            raise ValueError("INVALID_CRON")


def _require_playbook_payload(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    playbook_name = payload.get("playbook_name")
    target_asset_ids = payload.get("target_asset_ids")
    check_mode = payload.get("check_mode", False)
    if not isinstance(playbook_name, str) or not playbook_name.strip():
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    if not isinstance(target_asset_ids, list) or not target_asset_ids:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    if not isinstance(check_mode, bool):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return {
        "playbook_name": playbook_name.strip(),
        "target_asset_ids": list(target_asset_ids),
        "check_mode": check_mode,
    }


def _require_command_payload(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    command = payload.get("command")
    target_asset_ids = payload.get("target_asset_ids")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    if len(command) > MAX_COMMAND_LENGTH:
        raise ValueError("ADHOC_COMMAND_TOO_LONG")
    if not isinstance(target_asset_ids, list) or not target_asset_ids:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return {"command": command.strip(), "target_asset_ids": list(target_asset_ids)}


def _require_keys(payload: Mapping[str, Any], *, required: set[str]) -> dict[str, JsonValue]:
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return {key: payload[key] for key in payload}


async def list_job_definitions(db: AsyncSession, *, tenant_id: str) -> list[JobDefinition]:
    result = await db.execute(
        select(JobDefinition)
        .where(JobDefinition.tenant_id == tenant_id)
        .order_by(JobDefinition.created_at.desc())
    )
    return list(result.scalars().all())


async def get_job_definition(
    db: AsyncSession, *, tenant_id: str, job_id: str
) -> JobDefinition | None:
    result = await db.execute(
        select(JobDefinition).where(
            JobDefinition.id == job_id, JobDefinition.tenant_id == tenant_id
        )
    )
    return result.scalar_one_or_none()


async def enqueue_job_definition(
    *,
    db: AsyncSession,
    queue: AutomationJobQueue,
    definition: JobDefinition,
    actor: dict[str, Any],
    extra_override: Mapping[str, Any] | None = None,
) -> AutomationJobRun:
    """入队一次作业执行并落 queued 记录；失败模式为敏感键 / 类型不支持。"""

    requested_by = definition.run_as_user_id or str(actor.get("id") or "")
    payload = build_enqueue_payload(definition, extra_override=extra_override)
    job_type = queue_job_type(definition.job_type)
    message_id = await queue.enqueue(
        tenant_id=definition.tenant_id,
        job_type=job_type,
        requested_by=requested_by,
        payload=payload,
    )
    run = AutomationJobRun(
        message_id=message_id,
        tenant_id=definition.tenant_id,
        job_type=definition.job_type,
        status="queued",
        requested_by=requested_by,
        playbook_name=str(payload.get("playbook_name") or "") or None,
        check_mode=bool(payload.get("check_mode")) if "check_mode" in payload else None,
        target_count=_target_count(payload),
        job_definition_id=definition.id,
        extra_variables=payload.get("extra_variables") if isinstance(payload.get("extra_variables"), dict) else {},
        run_as_user_id=definition.run_as_user_id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def dispatch_due_cron_jobs(
    *,
    db: AsyncSession,
    queue: AutomationJobQueue,
    now: datetime,
) -> list[str]:
    """扫描到期周期作业并入队；成功后推进 ``next_run_at``。"""

    due = await db.execute(
        select(JobDefinition).where(
            JobDefinition.enabled.is_(True),
            JobDefinition.cron_expression.is_not(None),
            JobDefinition.next_run_at.is_not(None),
            JobDefinition.next_run_at <= now,
        )
    )
    dispatched: list[str] = []
    for definition in due.scalars().all():
        if definition.cron_expression:
            definition.next_run_at = next_cron_run(definition.cron_expression, after=now)
        actor = {"id": definition.run_as_user_id or definition.created_by, "permissions": ["admin"]}
        await enqueue_job_definition(db=db, queue=queue, definition=definition, actor=actor)
        dispatched.append(definition.id)
    return dispatched


def _target_count(payload: Mapping[str, JsonValue]) -> int | None:
    targets = payload.get("target_asset_ids")
    if isinstance(targets, list):
        return len(targets)
    return None
