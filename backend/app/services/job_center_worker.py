"""#t77 作业中心 worker：从 DB 加载作业定义，复用 Ansible runner，队列仅 JSON job_id。"""

from __future__ import annotations

import json
from collections.abc import Awaitable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.automation import AutomationJobRun
from app.models.job_center import OpsJob
from app.services.ansible_playbook import (
    AnsiblePlaybookRun,
    AnsiblePlaybookRunner,
    AnsiblePlaybookTarget,
)
from app.services.automation_worker import JsonValue
from app.services.job_center import (
    JOB_KIND_ADHOC,
    JOB_KIND_PLAYBOOK,
    adhoc_inline_playbook,
    get_active_assets,
    get_runas_account,
    get_scoped_playbook,
    load_int_list,
    merge_job_extra_vars,
    queue_job_type,
)


class JobCenterWorkerHandler:
    """消费 job.playbook / job.adhoc。凭据不进 argv/环境；runas 只提供 ansible_user。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runner: AnsiblePlaybookRunner,
    ) -> None:
        self._session_factory = session_factory
        self._runner = runner

    def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> Awaitable[None]:
        return self.handle(
            tenant_id=tenant_id,
            requested_by=requested_by,
            payload=payload,
            message_id=message_id,
        )

    async def handle(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        job_id = payload.get("job_id")
        if isinstance(job_id, bool) or not isinstance(job_id, int):
            raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
        extra_var_keys: list[str] = []
        playbook_name = ""
        check_mode = False
        target_count = 0
        job_kind = ""
        runas_account_id = 0
        try:
            run, extra_var_keys, playbook_name, job_kind, runas_account_id = await self._build_run(
                tenant_id=tenant_id,
                requested_by=requested_by,
                job_id=job_id,
            )
            target_count = len(run.targets)
            await self._record_run(
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                playbook_name=playbook_name,
                check_mode=check_mode,
                target_count=target_count,
                status="running",
                error_code=None,
                ops_job_id=job_id,
                job_kind=job_kind,
                runas_account_id=runas_account_id,
                extra_var_keys=extra_var_keys,
            )
            await self._runner.run(run)
        except Exception as exc:
            await self._record_run(
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                playbook_name=playbook_name,
                check_mode=check_mode,
                target_count=target_count,
                status="failed",
                error_code=_safe_error_code(exc),
                ops_job_id=job_id,
                job_kind=job_kind,
                runas_account_id=runas_account_id,
                extra_var_keys=extra_var_keys,
            )
            raise
        await self._record_run(
            message_id=message_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=check_mode,
            target_count=target_count,
            status="completed",
            error_code=None,
            ops_job_id=job_id,
            job_kind=job_kind,
            runas_account_id=runas_account_id,
            extra_var_keys=extra_var_keys,
        )

    async def _build_run(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        job_id: int,
    ) -> tuple[AnsiblePlaybookRun, list[str], str, str, int]:
        async with self._session_factory() as session:
            job = await session.get(OpsJob, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise ValueError("JOB_NOT_FOUND")
            if queue_job_type(job.job_kind) not in {"job.playbook", "job.adhoc"}:
                raise ValueError("UNSUPPORTED_JOB_KIND")
            asset_ids = load_int_list(job.target_asset_ids_json)
            assets = await get_active_assets(session, tenant_id=tenant_id, asset_ids=asset_ids)
            account = await get_runas_account(
                session, tenant_id=tenant_id, account_id=job.runas_account_id
            )
            extra_vars: dict[str, JsonValue] = await merge_job_extra_vars(session, job)
            playbook_name = ""
            inline_playbook = None
            if job.job_kind == JOB_KIND_PLAYBOOK:
                if job.playbook_id is None:
                    raise ValueError("PLAYBOOK_REQUIRED")
                playbook = await get_scoped_playbook(
                    session, tenant_id=tenant_id, playbook_id=job.playbook_id
                )
                playbook_name = playbook.filename
            elif job.job_kind == JOB_KIND_ADHOC:
                if job.adhoc_module not in {"shell", "command"} or not job.adhoc_command:
                    raise ValueError("ADHOC_COMMAND_INVALID")
                extra_vars = {
                    **extra_vars,
                    "janusgate_adhoc_module": job.adhoc_module,
                    "janusgate_adhoc_command": job.adhoc_command,
                }
                inline_playbook = adhoc_inline_playbook()
                playbook_name = "adhoc.yml"
            else:
                raise ValueError("UNSUPPORTED_JOB_KIND")
            targets = [
                AnsiblePlaybookTarget(
                    id=asset.id,
                    tenant_id=asset.tenant_id,
                    name=asset.name,
                    address=asset.address,
                    port=asset.port,
                    platform_id=asset.platform_id,
                )
                for asset in assets
            ]

        run = AnsiblePlaybookRun(
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=False,
            targets=targets,
            extra_vars=extra_vars,
            ansible_user=account.username,
            inline_playbook=inline_playbook,
        )
        return run, sorted(extra_vars.keys()), playbook_name, job.job_kind, account.id

    async def _record_run(
        self,
        *,
        message_id: str,
        tenant_id: str,
        requested_by: str,
        playbook_name: str,
        check_mode: bool,
        target_count: int,
        status: str,
        error_code: str | None,
        ops_job_id: int,
        job_kind: str,
        runas_account_id: int,
        extra_var_keys: list[str],
    ) -> None:
        async with self._session_factory() as session:
            run = await session.get(AutomationJobRun, message_id)
            if run is None:
                run = AutomationJobRun(
                    message_id=message_id,
                    tenant_id=tenant_id,
                    job_type=queue_job_type(job_kind) if job_kind else "job.playbook",
                    requested_by=requested_by,
                )
                session.add(run)
            run.status = status
            run.playbook_name = playbook_name or None
            run.check_mode = check_mode
            run.target_count = target_count
            run.error_code = error_code
            run.ops_job_id = ops_job_id
            run.job_kind = job_kind or None
            run.runas_account_id = runas_account_id or None
            run.extra_var_keys = json.dumps(extra_var_keys, separators=(",", ":"))
            await session.commit()


def _safe_error_code(exc: Exception) -> str:
    if exc.args and isinstance(exc.args[0], str) and exc.args[0].isupper():
        return exc.args[0][:120]
    return exc.__class__.__name__[:120]
