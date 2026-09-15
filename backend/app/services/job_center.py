"""#t77 作业中心服务：校验、参数化、入队与周期 tick。

职责：把 Playbook / AdHoc / Variable / runas 收敛成 #t52 JSON-only 队列 payload。
失败模式：敏感键、路径逃逸、shell 元字符、未知变量或跨租户资源一律拒绝。
约束：禁止 pickle；凭据不得进入队列，runas 只带 account_id。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.account import Account
from app.models.asset import Asset
from app.models.job_center import Job, JobExecution, JobPlaybook, JobVariable
from app.services.automation_worker import (
    ALLOWED_JOB_TYPES,
    AutomationJobQueue,
    JsonValue,
    _assert_no_sensitive_payload_keys,
)

JOB_KIND_PLAYBOOK = "playbook"
JOB_KIND_ADHOC = "adhoc"
ALLOWED_JOB_KINDS: frozenset[str] = frozenset({JOB_KIND_PLAYBOOK, JOB_KIND_ADHOC})
ALLOWED_ADHOC_MODULES: frozenset[str] = frozenset({"command"})
ADHOC_JOB_TYPE = "job.adhoc"
PLAYBOOK_CONTENT_MAX_BYTES = 64 * 1024
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 7 * 24 * 3600
_ADHOC_FORBIDDEN = re.compile(r"[|;&`$<>\n\r]|\$\(")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def validate_playbook_filename(filename: str) -> str:
    """校验 Playbook 相对路径：必须是 `.yml`/`.yaml`，禁止绝对路径与 `..`。"""

    name = filename.strip()
    candidate = Path(name)
    if (
        not name
        or candidate.is_absolute()
        or candidate.suffix not in {".yml", ".yaml"}
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
    ):
        raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
    return name


def validate_playbook_content(content: str) -> str:
    """限制 Playbook YAML 体积，避免把过大内容写入队列旁路的目录表。"""

    if len(content.encode("utf-8")) > PLAYBOOK_CONTENT_MAX_BYTES:
        raise ValueError("JOB_PLAYBOOK_CONTENT_TOO_LARGE")
    return content


def validate_adhoc_command(*, module: str, args: str) -> tuple[str, str]:
    """只允许 ansible `command` 模块，并拒绝 shell 元字符（避免变相 shell=True）。"""

    normalized = module.strip().lower() or "command"
    if normalized not in ALLOWED_ADHOC_MODULES:
        raise ValueError("ADHOC_MODULE_NOT_ALLOWED")
    command = args.strip()
    if not command:
        raise ValueError("ADHOC_ARGS_REQUIRED")
    if len(command) > 1024:
        raise ValueError("ADHOC_ARGS_TOO_LONG")
    if _ADHOC_FORBIDDEN.search(command):
        raise ValueError("ADHOC_ARGS_NOT_ALLOWED")
    return normalized, command


def coerce_extra_vars(value: object) -> dict[str, JsonValue]:
    """把 extra vars 收成 JSON 对象，并拒绝敏感键。"""

    if not isinstance(value, dict):
        raise ValueError("JOB_VARIABLE_INVALID")
    extra_vars: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("JOB_VARIABLE_INVALID")
        extra_vars[key] = _coerce_json_value(item)
    try:
        json.dumps(extra_vars, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("JOB_VARIABLE_INVALID") from exc
    _assert_no_sensitive_payload_keys(extra_vars)
    return extra_vars


def validate_variable_name(name: str) -> str:
    """变量名需可作 Ansible extra-var 标识符。"""

    cleaned = name.strip()
    if not _VARIABLE_NAME.fullmatch(cleaned):
        raise ValueError("JOB_VARIABLE_NAME_INVALID")
    return cleaned


def validate_interval_seconds(interval_seconds: int | None) -> int | None:
    """周期间隔可选；若设置则必须在 60s～7d。"""

    if interval_seconds is None:
        return None
    if interval_seconds < MIN_INTERVAL_SECONDS or interval_seconds > MAX_INTERVAL_SECONDS:
        raise ValueError("JOB_INTERVAL_INVALID")
    return interval_seconds


async def resolve_extra_vars(
    session: AsyncSession,
    *,
    tenant_id: str,
    names: list[str],
) -> tuple[list[str], dict[str, JsonValue]]:
    """按租户解析变量名；缺失则失败。返回已排序的名字与合并后的 extra_vars。"""

    extra_var_names = [validate_variable_name(name) for name in names]
    if not extra_var_names:
        return [], {}
    result = await session.execute(
        select(JobVariable)
        .where(JobVariable.tenant_id == tenant_id)
        .where(JobVariable.name.in_(extra_var_names))
    )
    rows = {row.name: row for row in result.scalars().all()}
    missing = [name for name in extra_var_names if name not in rows]
    if missing:
        raise ValueError("JOB_VARIABLE_NOT_FOUND")
    merged: dict[str, JsonValue] = {}
    for name in extra_var_names:
        merged.update(coerce_extra_vars(rows[name].extra_vars))
    return extra_var_names, merged


async def resolve_runas_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    account_id: int | None,
) -> int | None:
    """确认 runas 账号属于当前租户；不读取 secret_id 进入队列。"""

    if account_id is None:
        return None
    result = await session.execute(
        select(Account).where(Account.id == account_id).where(Account.tenant_id == tenant_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("RUNAS_ACCOUNT_NOT_FOUND")
    return account.id


async def ensure_active_assets(
    session: AsyncSession,
    *,
    tenant_id: str,
    asset_ids: list[int],
) -> list[int]:
    """确认目标资产均属于当前租户且 active。"""

    if not asset_ids:
        raise ValueError("JOB_TARGETS_REQUIRED")
    if any(
        not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id <= 0
        for asset_id in asset_ids
    ):
        raise ValueError("JOB_TARGETS_INVALID")
    result = await session.execute(
        select(Asset.id)
        .where(Asset.id.in_(asset_ids))
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
    )
    found = {row[0] for row in result.all()}
    if any(asset_id not in found for asset_id in asset_ids):
        raise ValueError("ASSET_NOT_FOUND")
    return list(asset_ids)


async def build_job_payload(
    session: AsyncSession,
    job: Job,
) -> tuple[str, dict[str, JsonValue]]:
    """把作业定义编译成 JSON-only 队列 payload，不携带凭据。"""

    extra_var_names, extra_vars = await resolve_extra_vars(
        session,
        tenant_id=job.tenant_id,
        names=list(job.extra_var_names or []),
    )
    if job.kind not in ALLOWED_JOB_KINDS:
        raise ValueError("JOB_KIND_INVALID")
    runas_account_id = await resolve_runas_account(
        session,
        tenant_id=job.tenant_id,
        account_id=job.runas_account_id,
    )
    target_asset_ids = await ensure_active_assets(
        session,
        tenant_id=job.tenant_id,
        asset_ids=list(job.target_asset_ids or []),
    )
    payload: dict[str, JsonValue] = {
        "job_id": job.id,
        "target_asset_ids": cast(list[JsonValue], list(target_asset_ids)),
        "extra_var_names": cast(list[JsonValue], list(extra_var_names)),
        "extra_vars": extra_vars,
        "check_mode": bool(job.check_mode),
    }
    if runas_account_id is not None:
        payload["runas_account_id"] = runas_account_id
    if job.kind == JOB_KIND_PLAYBOOK:
        if job.playbook_id is None:
            raise ValueError("JOB_PLAYBOOK_REQUIRED")
        playbook = await session.get(JobPlaybook, job.playbook_id)
        if playbook is None or playbook.tenant_id != job.tenant_id:
            raise ValueError("JOB_PLAYBOOK_NOT_FOUND")
        payload["playbook_name"] = playbook.filename
        payload["job_playbook_id"] = playbook.id
        return "ansible.playbook", payload
    module, args = validate_adhoc_command(module=job.adhoc_module, args=job.adhoc_args)
    payload["module"] = module
    payload["args"] = args
    return ADHOC_JOB_TYPE, payload


async def enqueue_job(
    *,
    session: AsyncSession,
    queue: AutomationJobQueue,
    job: Job,
    requested_by: str,
) -> JobExecution:
    """入队作业并写 JobExecution。队列类型必须仍在 JSON-only 白名单内。"""

    job_type, payload = await build_job_payload(session, job)
    if job_type not in ALLOWED_JOB_TYPES:
        raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")
    message_id = await queue.enqueue(
        tenant_id=job.tenant_id,
        job_type=job_type,
        requested_by=requested_by,
        payload=payload,
    )
    execution = JobExecution(
        tenant_id=job.tenant_id,
        job_id=job.id,
        message_id=message_id,
        status="queued",
        requested_by=requested_by,
    )
    session.add(execution)
    await session.flush()
    return execution


async def tick_due_jobs(
    *,
    session: AsyncSession,
    queue: AutomationJobQueue,
    tenant_id: str,
    requested_by: str,
    now: datetime | None = None,
) -> list[JobExecution]:
    """把到期的周期作业入队，并把 next_run_at 推到下一窗口。"""

    current = now or datetime.now(UTC)
    result = await session.execute(
        select(Job)
        .where(Job.tenant_id == tenant_id)
        .where(Job.enabled.is_(True))
        .where(Job.interval_seconds.is_not(None))
        .where(Job.next_run_at.is_not(None))
        .where(Job.next_run_at <= current)
        .order_by(Job.id.asc())
    )
    executions: list[JobExecution] = []
    for job in result.scalars().all():
        interval = job.interval_seconds
        if interval is None:
            continue
        execution = await enqueue_job(
            session=session,
            queue=queue,
            job=job,
            requested_by=requested_by,
        )
        job.next_run_at = current + timedelta(seconds=interval)
        executions.append(execution)
    return executions


async def sync_job_execution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    message_id: str,
    status: str,
    error_code: str | None,
) -> None:
    """按 Redis message_id 回写 JobExecution；旧的 /playbooks 直入队路径无记录则忽略。"""

    async with session_factory() as session:
        result = await session.execute(
            select(JobExecution).where(JobExecution.message_id == message_id)
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            return
        execution.status = status
        execution.error_code = error_code
        await session.commit()


def next_run_at_for_interval(
    interval_seconds: int | None, *, now: datetime | None = None
) -> datetime | None:
    """新建周期作业时立刻到期，让随后的 tick 可以拾取。"""

    if interval_seconds is None:
        return None
    return now or datetime.now(UTC)


def _coerce_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        nested: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("JOB_VARIABLE_INVALID")
            nested[key] = _coerce_json_value(item)
        return nested
    if isinstance(value, list):
        return [_coerce_json_value(item) for item in value]
    raise ValueError("JOB_VARIABLE_INVALID")
