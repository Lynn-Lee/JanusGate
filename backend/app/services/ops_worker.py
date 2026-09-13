"""#t77 作业中心 worker：按 execution_id 从库加载规格后调用 Ansible runner。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.account import Account
from app.models.asset import Asset
from app.models.automation import AutomationJobRun
from app.models.ops import OpsJobExecution
from app.services.ansible_playbook import (
    AnsiblePlaybookRun,
    AnsiblePlaybookRunner,
    AnsiblePlaybookTarget,
    _safe_error_code,
)
from app.services.automation_worker import JsonValue
from app.services.ops_sanitize import ADHOC_PLAYBOOK, loads_ids, loads_vars


class OpsJobWorkerHandler:
    """消费 ``ops.job``。凭据不进 runner；inventory 最多带 ansible_user。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runner: AnsiblePlaybookRunner,
    ) -> None:
        self._session_factory = session_factory
        self._runner = runner

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        execution_id = payload.get("execution_id")
        if isinstance(execution_id, bool) or not isinstance(execution_id, int):
            raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
        check_mode = payload.get("check_mode")
        if not isinstance(check_mode, bool):
            check_mode = False

        async with self._session_factory() as session:
            execution = await session.get(OpsJobExecution, execution_id)
            if execution is None or execution.tenant_id != tenant_id:
                raise ValueError("OPS_EXECUTION_NOT_FOUND")
            account = await session.get(Account, execution.runas_account_id)
            if account is None or account.tenant_id != tenant_id or account.protocol.lower() != "ssh":
                raise ValueError("OPS_RUNAS_NOT_FOUND")
            asset_ids = loads_ids(execution.target_asset_ids_json)
            assets = (
                await session.execute(
                    select(Asset)
                    .where(Asset.id.in_(asset_ids))
                    .where(Asset.tenant_id == tenant_id)
                    .where(Asset.is_active.is_(True))
                )
            ).scalars().all()
            assets_by_id = {asset.id: asset for asset in assets}
            if any(asset_id not in assets_by_id for asset_id in asset_ids):
                raise ValueError("ASSET_NOT_FOUND")
            targets = [
                AnsiblePlaybookTarget(
                    id=asset.id,
                    tenant_id=asset.tenant_id,
                    name=asset.name,
                    address=asset.address,
                    port=asset.port,
                    platform_id=asset.platform_id,
                )
                for asset in (assets_by_id[asset_id] for asset_id in asset_ids)
            ]
            extra = loads_vars(execution.extra_vars_json)
            if execution.job_type == "adhoc":
                extra = {
                    **extra,
                    "adhoc_module": execution.adhoc_module or "command",
                    "adhoc_command": execution.command or "",
                }
            playbook_name = execution.playbook_name or ADHOC_PLAYBOOK
            ansible_user = account.username
            execution.status = "running"
            await session.commit()

        run = AnsiblePlaybookRun(
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=check_mode,
            targets=targets,
            extra_vars=extra,
            ansible_user=ansible_user,
        )
        await self._record_run(
            message_id=message_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=check_mode,
            target_count=len(targets),
            status="running",
            error_code=None,
        )
        try:
            await self._runner.run(run)
        except Exception as exc:
            await self._mark(execution_id, "failed", _safe_error_code(exc))
            await self._record_run(
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                playbook_name=playbook_name,
                check_mode=check_mode,
                target_count=len(targets),
                status="failed",
                error_code=_safe_error_code(exc),
            )
            raise
        await self._mark(execution_id, "completed", None)
        await self._record_run(
            message_id=message_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=check_mode,
            target_count=len(targets),
            status="completed",
            error_code=None,
        )

    async def _mark(self, execution_id: int, status: str, error_code: str | None) -> None:
        async with self._session_factory() as session:
            execution = await session.get(OpsJobExecution, execution_id)
            if execution is None:
                return
            execution.status = status
            execution.error_code = error_code
            await session.commit()

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
    ) -> None:
        async with self._session_factory() as session:
            run = await session.get(AutomationJobRun, message_id)
            if run is None:
                run = AutomationJobRun(
                    message_id=message_id,
                    tenant_id=tenant_id,
                    job_type="ops.job",
                    requested_by=requested_by,
                )
                session.add(run)
            run.status = status
            run.playbook_name = playbook_name
            run.check_mode = check_mode
            run.target_count = target_count
            run.error_code = error_code
            await session.commit()
