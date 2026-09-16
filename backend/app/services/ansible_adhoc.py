"""#t77 Ansible ad-hoc 执行器：仅 `command` 模块，JSON-only，无 pickle / shell=True。

职责：消费 `job.adhoc` 队列消息，按租户确认目标资产后调用本地 `ansible`。
失败模式：未知模块、shell 元字符、跨租户资产、runas 账号缺失。
凭据不进入 argv / env / inventory；runas 只写入 ansible_user。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, settings
from app.models.automation import AutomationJobRun
from app.services.ansible_playbook import (
    AnsibleCommandRunner,
    AnsiblePlaybookTarget,
    AnsibleProcessLimits,
    _build_inventory,
    _get_active_assets,
    _optional_megabytes,
    _optional_positive_int,
    _payload_bool,
    _payload_int_list,
    _payload_object,
    _payload_optional_int,
    _payload_str,
    _resolve_runas_username,
    _run_ansible_command,
    _safe_ansible_env,
    _safe_error_code,
)
from app.services.automation_worker import JsonValue, _assert_no_sensitive_payload_keys
from app.services.job_center import sync_job_execution, validate_adhoc_command


@dataclass(frozen=True)
class AnsibleAdhocRun:
    tenant_id: str
    requested_by: str
    module: str
    args: str
    check_mode: bool
    targets: list[AnsiblePlaybookTarget]
    extra_vars: dict[str, JsonValue] = field(default_factory=dict)
    runas_username: str | None = None


class AnsibleAdhocRunner(Protocol):
    def run(self, adhoc: AnsibleAdhocRun) -> Awaitable[None]: ...


class LocalAnsibleAdhocRunner:
    """本地 `ansible` ad-hoc adapter：临时 JSON inventory + extra-vars 文件，不继承 secret 环境。"""

    def __init__(
        self,
        *,
        runtime_root: Path,
        command_runner: AnsibleCommandRunner | None = None,
        executable: str = "ansible",
        timeout_seconds: float = 300.0,
        process_limits: AnsibleProcessLimits | None = None,
    ) -> None:
        self._runtime_root = runtime_root.resolve()
        self._command_runner = command_runner or _run_ansible_command
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._process_limits = process_limits or AnsibleProcessLimits()

    async def run(self, adhoc: AnsibleAdhocRun) -> None:
        module, args = validate_adhoc_command(module=adhoc.module, args=adhoc.args)
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"janusgate-ansible-adhoc-{adhoc.tenant_id}-",
            dir=self._runtime_root,
        ) as work_dir_name:
            work_dir = Path(work_dir_name)
            inventory_path = work_dir / "inventory.json"
            inventory_path.write_text(
                json.dumps(
                    _build_inventory(adhoc.targets, runas_username=adhoc.runas_username),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            argv = [
                self._executable,
                "all",
                "-i",
                str(inventory_path),
                "-m",
                module,
                "-a",
                args,
            ]
            if adhoc.check_mode:
                argv.append("--check")
            if adhoc.extra_vars:
                extra_vars_path = work_dir / "extra_vars.json"
                extra_vars_path.write_text(
                    json.dumps(adhoc.extra_vars, sort_keys=True),
                    encoding="utf-8",
                )
                argv.extend(["-e", f"@{extra_vars_path}"])
            try:
                result = await asyncio.wait_for(
                    self._command_runner(
                        argv,
                        cwd=work_dir,
                        env=_safe_ansible_env(),
                        process_limits=self._process_limits,
                    ),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                raise ValueError("ANSIBLE_ADHOC_TIMED_OUT") from exc
            if result != 0:
                raise ValueError("ANSIBLE_ADHOC_FAILED")


def build_local_ansible_adhoc_runner(
    *,
    settings: Settings = settings,
    command_runner: AnsibleCommandRunner | None = None,
) -> LocalAnsibleAdhocRunner:
    return LocalAnsibleAdhocRunner(
        runtime_root=Path(settings.ANSIBLE_RUNTIME_ROOT),
        command_runner=command_runner,
        timeout_seconds=settings.ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS,
        process_limits=AnsibleProcessLimits(
            memory_limit_bytes=_optional_megabytes(settings.ANSIBLE_PLAYBOOK_MEMORY_LIMIT_MB),
            cpu_limit_seconds=_optional_positive_int(settings.ANSIBLE_PLAYBOOK_CPU_LIMIT_SECONDS),
        ),
    )


class AnsibleAdhocWorkerHandler:
    """消费 `job.adhoc`：租户校验资产与 runas，写 AutomationJobRun / JobExecution。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runner: AnsibleAdhocRunner,
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
        module, args = validate_adhoc_command(
            module=_payload_str(payload, "module"),
            args=_payload_str(payload, "args"),
        )
        target_asset_ids = _payload_int_list(payload, "target_asset_ids")
        check_mode = _payload_bool(payload, "check_mode") if "check_mode" in payload else False
        extra_vars = _payload_object(payload, "extra_vars")
        _assert_no_sensitive_payload_keys(extra_vars)
        runas_account_id = _payload_optional_int(payload, "runas_account_id")

        async with self._session_factory() as session:
            assets = await _get_active_assets(
                session,
                tenant_id=tenant_id,
                asset_ids=target_asset_ids,
            )
            assets_by_id = {asset.id: asset for asset in assets}
            if any(asset_id not in assets_by_id for asset_id in target_asset_ids):
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
                for asset in (assets_by_id[asset_id] for asset_id in target_asset_ids)
            ]
            runas_username = await _resolve_runas_username(
                session,
                tenant_id=tenant_id,
                account_id=runas_account_id,
            )

        run = AnsibleAdhocRun(
            tenant_id=tenant_id,
            requested_by=requested_by,
            module=module,
            args=args,
            check_mode=bool(check_mode),
            targets=targets,
            extra_vars=extra_vars,
            runas_username=runas_username,
        )
        playbook_name = f"adhoc:{module}"
        await self._record_run(
            message_id=message_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=run.check_mode,
            target_count=len(targets),
            status="running",
            error_code=None,
        )
        await sync_job_execution(
            self._session_factory,
            message_id=message_id,
            status="running",
            error_code=None,
        )
        try:
            await self._runner.run(run)
        except Exception as exc:
            error_code = _safe_error_code(exc)
            await self._record_run(
                message_id=message_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                playbook_name=playbook_name,
                check_mode=run.check_mode,
                target_count=len(targets),
                status="failed",
                error_code=error_code,
            )
            await sync_job_execution(
                self._session_factory,
                message_id=message_id,
                status="failed",
                error_code=error_code,
            )
            raise
        await self._record_run(
            message_id=message_id,
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=run.check_mode,
            target_count=len(targets),
            status="completed",
            error_code=None,
        )
        await sync_job_execution(
            self._session_factory,
            message_id=message_id,
            status="completed",
            error_code=None,
        )

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
                    job_type="job.adhoc",
                    requested_by=requested_by,
                )
                session.add(run)
            run.status = status
            run.playbook_name = playbook_name
            run.check_mode = check_mode
            run.target_count = target_count
            run.error_code = error_code
            await session.commit()
