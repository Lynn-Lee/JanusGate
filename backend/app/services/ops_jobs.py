"""#t77 作业中心调度：创建执行记录并入 JSON-only 队列。

队列 payload 只含 ``execution_id``。命令、变量、runas 从执行记录加载，避免 pickle
与 argv 拼凭据。临时命令在入队前走命令过滤 ACL；DENY / REVIEW 均拒绝。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.ops import OpsJob, OpsJobExecution, OpsJobVariable, OpsPlaybook
from app.policy.repository import build_tenant_policy_service
from app.policy.schemas import CommandDecisionRequest, CommandFilterEffect, ResourceRef, SubjectRef
from app.services.automation_worker import AutomationJobQueue, JsonValue
from app.services.ops_sanitize import (
    ADHOC_PLAYBOOK,
    OpsConfigError,
    adhoc_command,
    adhoc_module,
    cron_expr,
    extra_vars,
    extra_vars_json,
    loads_ids,
    loads_vars,
    next_cron_run,
    parse_asset_ids,
    playbook_relative_path,
)
from app.tenancy.scope import ActorScope, actor_scope_from_user, scoped_select


class OpsJobService:
    """作业中心写路径。失败抛 ``OpsConfigError`` 或 ``ValueError`` 稳定码。"""

    def __init__(self, db: AsyncSession, queue: AutomationJobQueue) -> None:
        self._db = db
        self._queue = queue

    async def create_playbook(
        self,
        *,
        user: dict[str, Any],
        name: str,
        relative_path: str,
        description: str = "",
    ) -> OpsPlaybook:
        tenant_id = _tenant(user)
        playbook = OpsPlaybook(
            tenant_id=tenant_id,
            name=name.strip(),
            relative_path=playbook_relative_path(relative_path),
            description=description.strip(),
            status="active",
        )
        if not playbook.name:
            raise OpsConfigError("OPS_PLAYBOOK_NAME_REQUIRED")
        self._db.add(playbook)
        await self._db.commit()
        await self._db.refresh(playbook)
        return playbook

    async def create_job(
        self,
        *,
        user: dict[str, Any],
        name: str,
        job_type: str,
        runas_account_id: int,
        target_asset_ids: list[int],
        playbook_id: int | None = None,
        adhoc_module_name: str | None = None,
        command: str | None = None,
        extra: dict[str, Any] | None = None,
        variables: list[dict[str, Any]] | None = None,
        cron: str | None = None,
        check_mode: bool = False,
    ) -> OpsJob:
        tenant_id = _tenant(user)
        kind = job_type.strip().lower()
        if kind not in {"playbook", "adhoc"}:
            raise OpsConfigError("OPS_JOB_TYPE_INVALID")
        asset_ids = parse_asset_ids(target_asset_ids)
        await self._require_assets(user, asset_ids)
        account = await self._require_runas(user, runas_account_id)
        playbook_fk: int | None = None
        module: str | None = None
        cmd: str | None = None
        if kind == "playbook":
            playbook = await self._get_playbook(user, playbook_id)
            playbook_fk = playbook.id
        else:
            module = adhoc_module(adhoc_module_name)
            cmd = adhoc_command(command)
            await self._require_command_allowed(user, account, asset_ids, cmd)
        schedule = cron_expr(cron)
        now = datetime.now(UTC)
        job = OpsJob(
            tenant_id=tenant_id,
            name=name.strip(),
            job_type=kind,
            playbook_id=playbook_fk,
            adhoc_module=module,
            command=cmd,
            target_asset_ids_json=json.dumps(asset_ids),
            extra_vars_json=extra_vars_json(extra),
            runas_account_id=account.id,
            cron_expr=schedule,
            next_run_at=next_cron_run(schedule, after=now) if schedule else None,
            check_mode=check_mode,
            status="active",
        )
        if not job.name:
            raise OpsConfigError("OPS_JOB_NAME_REQUIRED")
        self._db.add(job)
        await self._db.flush()
        for item in variables or []:
            var_name = str(item.get("name") or "").strip()
            if not var_name:
                raise OpsConfigError("OPS_VARIABLE_NAME_REQUIRED")
            extra_vars({var_name: str(item.get("default_value") or "")})
            self._db.add(
                OpsJobVariable(
                    job_id=job.id,
                    name=var_name,
                    default_value=str(item.get("default_value") or ""),
                    required=bool(item.get("required") or False),
                )
            )
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def run_job(
        self,
        *,
        user: dict[str, Any],
        job_id: int,
        extra: dict[str, Any] | None = None,
    ) -> OpsJobExecution:
        job = await self._get_job(user, job_id)
        merged = {**await self._defaults(job), **loads_vars(job.extra_vars_json), **extra_vars(extra)}
        playbook_name, module, command = await self._execution_spec(user, job)
        if job.job_type == "adhoc" and command:
            account = await self._require_runas(user, job.runas_account_id)
            await self._require_command_allowed(user, account, loads_ids(job.target_asset_ids_json), command)
        return await self._enqueue_execution(
            user=user,
            job=job,
            job_type=job.job_type,
            playbook_name=playbook_name,
            module=module,
            command=command,
            asset_ids=loads_ids(job.target_asset_ids_json),
            extra=merged,
            runas_account_id=job.runas_account_id,
            check_mode=job.check_mode,
        )

    async def run_adhoc(
        self,
        *,
        user: dict[str, Any],
        runas_account_id: int,
        target_asset_ids: list[int],
        module_name: str,
        command: str,
        extra: dict[str, Any] | None = None,
        check_mode: bool = False,
    ) -> OpsJobExecution:
        asset_ids = parse_asset_ids(target_asset_ids)
        await self._require_assets(user, asset_ids)
        account = await self._require_runas(user, runas_account_id)
        module = adhoc_module(module_name)
        cmd = adhoc_command(command)
        await self._require_command_allowed(user, account, asset_ids, cmd)
        return await self._enqueue_execution(
            user=user,
            job=None,
            job_type="adhoc",
            playbook_name=ADHOC_PLAYBOOK,
            module=module,
            command=cmd,
            asset_ids=asset_ids,
            extra=extra_vars(extra),
            runas_account_id=account.id,
            check_mode=check_mode,
        )

    async def tick(self, *, user: dict[str, Any], now: datetime | None = None) -> list[int]:
        """把当前租户到期的 cron 作业入队，并推进 next_run_at。"""
        moment = now or datetime.now(UTC)
        tenant_id = _tenant(user)
        result = await self._db.execute(
            select(OpsJob).where(
                OpsJob.tenant_id == tenant_id,
                OpsJob.status == "active",
                OpsJob.cron_expr.is_not(None),
                OpsJob.next_run_at.is_not(None),
                OpsJob.next_run_at <= moment,
            )
        )
        queued: list[int] = []
        for job in result.scalars().all():
            execution = await self.run_job(user=user, job_id=job.id)
            job.next_run_at = next_cron_run(str(job.cron_expr), after=moment)
            queued.append(execution.id)
        await self._db.commit()
        return queued

    async def _enqueue_execution(
        self,
        *,
        user: dict[str, Any],
        job: OpsJob | None,
        job_type: str,
        playbook_name: str,
        module: str | None,
        command: str | None,
        asset_ids: list[int],
        extra: dict[str, str | int | float | bool],
        runas_account_id: int,
        check_mode: bool,
    ) -> OpsJobExecution:
        tenant_id = _tenant(user)
        execution = OpsJobExecution(
            tenant_id=tenant_id,
            job_id=job.id if job is not None else None,
            job_type=job_type,
            playbook_name=playbook_name,
            command=command,
            adhoc_module=module,
            target_asset_ids_json=json.dumps(asset_ids),
            extra_vars_json=extra_vars_json(extra),
            runas_account_id=runas_account_id,
            requested_by=str(user["id"]),
            check_mode=check_mode,
            status="queued",
        )
        self._db.add(execution)
        await self._db.flush()
        payload: dict[str, JsonValue] = {
            "execution_id": execution.id,
            "check_mode": check_mode,
        }
        message_id = await self._queue.enqueue(
            tenant_id=tenant_id,
            requested_by=str(user["id"]),
            job_type="ops.job",
            payload=payload,
        )
        execution.message_id = message_id
        await self._db.commit()
        await self._db.refresh(execution)
        return execution

    async def _execution_spec(self, user: dict[str, Any], job: OpsJob) -> tuple[str, str | None, str | None]:
        if job.job_type == "playbook":
            playbook = await self._get_playbook(user, job.playbook_id)
            return playbook.relative_path, None, None
        return ADHOC_PLAYBOOK, job.adhoc_module, job.command

    async def _defaults(self, job: OpsJob) -> dict[str, str | int | float | bool]:
        result = await self._db.execute(select(OpsJobVariable).where(OpsJobVariable.job_id == job.id))
        merged: dict[str, str | int | float | bool] = {}
        for variable in result.scalars().all():
            if variable.required and variable.default_value == "":
                raise OpsConfigError("OPS_VARIABLE_REQUIRED")
            extra_vars({variable.name: variable.default_value})
            merged[variable.name] = variable.default_value
        return merged

    async def _get_playbook(self, user: dict[str, Any], playbook_id: int | None) -> OpsPlaybook:
        if playbook_id is None:
            raise OpsConfigError("OPS_PLAYBOOK_REQUIRED")
        result = await self._db.execute(
            select(OpsPlaybook).where(
                OpsPlaybook.id == playbook_id,
                OpsPlaybook.tenant_id == _tenant(user),
                OpsPlaybook.status == "active",
            )
        )
        playbook = result.scalar_one_or_none()
        if playbook is None:
            raise OpsConfigError("OPS_PLAYBOOK_NOT_FOUND")
        return playbook

    async def _get_job(self, user: dict[str, Any], job_id: int) -> OpsJob:
        result = await self._db.execute(
            select(OpsJob).where(OpsJob.id == job_id, OpsJob.tenant_id == _tenant(user))
        )
        job = result.scalar_one_or_none()
        if job is None or job.status != "active":
            raise OpsConfigError("OPS_JOB_NOT_FOUND")
        return job

    async def _require_runas(self, user: dict[str, Any], account_id: int) -> Account:
        result = await self._db.execute(
            scoped_select(Account, actor_scope_from_user(user)).where(
                Account.id == account_id,
                Account.status == "active",
            )
        )
        account = result.scalar_one_or_none()
        if account is None or account.protocol.lower() != "ssh":
            raise OpsConfigError("OPS_RUNAS_NOT_FOUND")
        return account

    async def _require_assets(self, user: dict[str, Any], asset_ids: list[int]) -> None:
        result = await self._db.execute(
            scoped_select(Asset, actor_scope_from_user(user)).where(
                Asset.id.in_(asset_ids),
                Asset.is_active.is_(True),
            )
        )
        found = {asset.id for asset in result.scalars().all()}
        if any(asset_id not in found for asset_id in asset_ids):
            raise OpsConfigError("OPS_TARGET_NOT_FOUND")

    async def _require_command_allowed(
        self,
        user: dict[str, Any],
        account: Account,
        asset_ids: list[int],
        command: str,
    ) -> None:
        scope: ActorScope = actor_scope_from_user(user)
        try:
            service = await build_tenant_policy_service(self._db, scope)
        except Exception as exc:
            raise OpsConfigError("OPS_COMMAND_POLICY_UNAVAILABLE") from exc
        for asset_id in asset_ids:
            decision = service.evaluate_command(
                CommandDecisionRequest(
                    subject=SubjectRef(id=str(user["id"]), tenant_id=_tenant(user)),
                    resource=ResourceRef(id=str(asset_id), type="asset", tenant_id=_tenant(user)),
                    account_id=str(account.id),
                    command=command,
                )
            )
            if decision.effect == CommandFilterEffect.DENY:
                raise OpsConfigError("OPS_COMMAND_DENIED")
            if decision.effect == CommandFilterEffect.REVIEW:
                raise OpsConfigError("OPS_COMMAND_REVIEW_REQUIRED")


def _tenant(user: dict[str, Any]) -> str:
    return str(user.get("tenant_id") or "default")
