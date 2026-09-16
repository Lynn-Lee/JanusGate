"""Ansible playbook automation worker handler."""

from __future__ import annotations

import asyncio
import json
import os
import resource
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.automation import AutomationJobRun
from app.models.job_center import JobPlaybook
from app.services.automation_worker import JsonValue, _assert_no_sensitive_payload_keys
from app.services.job_center import sync_job_execution


@dataclass(frozen=True)
class AnsiblePlaybookTarget:
    id: int
    tenant_id: str
    name: str
    address: str
    port: int
    platform_id: int


@dataclass(frozen=True)
class AnsiblePlaybookRun:
    tenant_id: str
    requested_by: str
    playbook_name: str
    check_mode: bool
    targets: list[AnsiblePlaybookTarget]
    extra_vars: dict[str, JsonValue] = field(default_factory=dict)
    runas_username: str | None = None
    playbook_content: str | None = None


@dataclass(frozen=True)
class AnsibleProcessLimits:
    memory_limit_bytes: int | None = None
    cpu_limit_seconds: int | None = None


class AnsiblePlaybookRunner(Protocol):
    def run(self, playbook: AnsiblePlaybookRun) -> Awaitable[None]: ...


class AnsibleCommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        process_limits: AnsibleProcessLimits,
    ) -> Awaitable[int]: ...


class LocalAnsiblePlaybookRunner:
    def __init__(
        self,
        *,
        playbook_root: Path,
        runtime_root: Path,
        command_runner: AnsibleCommandRunner | None = None,
        executable: str = "ansible-playbook",
        timeout_seconds: float = 300.0,
        process_limits: AnsibleProcessLimits | None = None,
    ) -> None:
        self._playbook_root = playbook_root.resolve()
        self._runtime_root = runtime_root.resolve()
        self._command_runner = command_runner or _run_ansible_command
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._process_limits = process_limits or AnsibleProcessLimits()

    async def run(self, playbook: AnsiblePlaybookRun) -> None:
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"janusgate-ansible-{playbook.tenant_id}-",
            dir=self._runtime_root,
        ) as work_dir_name:
            work_dir = Path(work_dir_name)
            if playbook.playbook_content is not None:
                playbook_path = self._write_playbook_content(
                    work_dir,
                    playbook.playbook_name,
                    playbook.playbook_content,
                )
            else:
                playbook_path = self._resolve_playbook(playbook.playbook_name)
            inventory_path = work_dir / "inventory.json"
            inventory_path.write_text(
                json.dumps(
                    _build_inventory(playbook.targets, runas_username=playbook.runas_username),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            args = [self._executable, str(playbook_path), "-i", str(inventory_path)]
            if playbook.check_mode:
                args.append("--check")
            if playbook.extra_vars:
                extra_vars_path = work_dir / "extra_vars.json"
                extra_vars_path.write_text(
                    json.dumps(playbook.extra_vars, sort_keys=True),
                    encoding="utf-8",
                )
                args.extend(["-e", f"@{extra_vars_path}"])
            try:
                result = await asyncio.wait_for(
                    self._command_runner(
                        args,
                        cwd=work_dir,
                        env=_safe_ansible_env(),
                        process_limits=self._process_limits,
                    ),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                raise ValueError("ANSIBLE_PLAYBOOK_TIMED_OUT") from exc
            if result != 0:
                raise ValueError("ANSIBLE_PLAYBOOK_FAILED")

    def _resolve_playbook(self, playbook_name: str) -> Path:
        candidate = Path(playbook_name)
        if candidate.is_absolute() or candidate.suffix not in {".yml", ".yaml"}:
            raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
        playbook_path = (self._playbook_root / candidate).resolve()
        if not playbook_path.is_relative_to(self._playbook_root) or not playbook_path.is_file():
            raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
        return playbook_path

    def _write_playbook_content(self, work_dir: Path, playbook_name: str, content: str) -> Path:
        """把目录中的 YAML 写到 runtime 临时目录，文件名只取 basename 以防路径逃逸。"""

        candidate = Path(playbook_name)
        if (
            candidate.is_absolute()
            or candidate.suffix not in {".yml", ".yaml"}
            or ".." in candidate.parts
        ):
            raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
        dest = (work_dir / candidate.name).resolve()
        if not dest.is_relative_to(work_dir.resolve()):
            raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
        dest.write_text(content, encoding="utf-8")
        return dest


def build_local_ansible_playbook_runner(
    *,
    settings: Settings = settings,
    command_runner: AnsibleCommandRunner | None = None,
) -> LocalAnsiblePlaybookRunner:
    return LocalAnsiblePlaybookRunner(
        playbook_root=Path(settings.ANSIBLE_PLAYBOOK_ROOT),
        runtime_root=Path(settings.ANSIBLE_RUNTIME_ROOT),
        command_runner=command_runner,
        executable=settings.ANSIBLE_PLAYBOOK_EXECUTABLE,
        timeout_seconds=settings.ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS,
        process_limits=AnsibleProcessLimits(
            memory_limit_bytes=_optional_megabytes(settings.ANSIBLE_PLAYBOOK_MEMORY_LIMIT_MB),
            cpu_limit_seconds=_optional_positive_int(settings.ANSIBLE_PLAYBOOK_CPU_LIMIT_SECONDS),
        ),
    )


class AnsiblePlaybookWorkerHandler:
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
        playbook_name = _payload_str(payload, "playbook_name")
        target_asset_ids = _payload_int_list(payload, "target_asset_ids")
        check_mode = _payload_bool(payload, "check_mode")
        extra_vars = _payload_object(payload, "extra_vars")
        _assert_no_sensitive_payload_keys(extra_vars)
        job_playbook_id = _payload_optional_int(payload, "job_playbook_id")
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
            playbook_content: str | None = None
            if job_playbook_id is not None:
                playbook = await session.get(JobPlaybook, job_playbook_id)
                if playbook is None or playbook.tenant_id != tenant_id:
                    raise ValueError("JOB_PLAYBOOK_NOT_FOUND")
                playbook_content = playbook.content
                playbook_name = playbook.filename
            runas_username = await _resolve_runas_username(
                session,
                tenant_id=tenant_id,
                account_id=runas_account_id,
            )

        run = AnsiblePlaybookRun(
            tenant_id=tenant_id,
            requested_by=requested_by,
            playbook_name=playbook_name,
            check_mode=check_mode,
            targets=targets,
            extra_vars=extra_vars,
            runas_username=runas_username,
            playbook_content=playbook_content,
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
                check_mode=check_mode,
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
            check_mode=check_mode,
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
                    job_type="ansible.playbook",
                    requested_by=requested_by,
                )
                session.add(run)
            run.status = status
            run.playbook_name = playbook_name
            run.check_mode = check_mode
            run.target_count = target_count
            run.error_code = error_code
            await session.commit()


async def _get_active_assets(
    session: AsyncSession,
    *,
    tenant_id: str,
    asset_ids: list[int],
) -> list[Asset]:
    result = await session.execute(
        select(Asset)
        .where(Asset.id.in_(asset_ids))
        .where(Asset.tenant_id == tenant_id)
        .where(Asset.is_active.is_(True))
    )
    return list(result.scalars().all())


async def _resolve_runas_username(
    session: AsyncSession,
    *,
    tenant_id: str,
    account_id: int | None,
) -> str | None:
    """解析 runas 用户名；只返回 username，不读取 secret_id。"""

    if account_id is None:
        return None
    result = await session.execute(
        select(Account).where(Account.id == account_id).where(Account.tenant_id == tenant_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("RUNAS_ACCOUNT_NOT_FOUND")
    return account.username


def _payload_bool(payload: dict[str, JsonValue], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_int_list(payload: dict[str, JsonValue], key: str) -> list[int]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    asset_ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
        asset_ids.append(item)
    return asset_ids


def _payload_str(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_optional_int(payload: dict[str, JsonValue], key: str) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_object(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    if key not in payload:
        return {}
    value = payload[key]
    if not isinstance(value, dict):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _safe_error_code(exc: Exception) -> str:
    if exc.args and isinstance(exc.args[0], str) and exc.args[0].isupper():
        return exc.args[0][:120]
    return exc.__class__.__name__[:120]


def _build_inventory(
    targets: list[AnsiblePlaybookTarget],
    *,
    runas_username: str | None = None,
) -> dict[str, object]:
    hosts: dict[str, dict[str, object]] = {}
    for target in targets:
        host: dict[str, object] = {
            "ansible_host": target.address,
            "ansible_port": target.port,
            "janusgate_asset_id": target.id,
            "janusgate_asset_name": target.name,
            "janusgate_platform_id": target.platform_id,
            "janusgate_tenant_id": target.tenant_id,
        }
        if runas_username:
            host["ansible_user"] = runas_username
        hosts[f"asset_{target.id}"] = host
    return {"all": {"hosts": hosts}}


def _safe_ansible_env() -> dict[str, str]:
    env: dict[str, str] = {
        "ANSIBLE_NOCOWS": "1",
        "ANSIBLE_RETRY_FILES_ENABLED": "False",
    }
    if path := os.environ.get("PATH"):
        env["PATH"] = path
    if lang := os.environ.get("LANG"):
        env["LANG"] = lang
    return env


def _optional_megabytes(value: int) -> int | None:
    if value <= 0:
        return None
    return value * 1024 * 1024


def _optional_positive_int(value: int) -> int | None:
    if value <= 0:
        return None
    return value


def _build_process_limit_preexec(process_limits: AnsibleProcessLimits) -> Callable[[], None] | None:
    if process_limits.memory_limit_bytes is None and process_limits.cpu_limit_seconds is None:
        return None

    def apply_limits() -> None:
        if process_limits.memory_limit_bytes is not None:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (process_limits.memory_limit_bytes, process_limits.memory_limit_bytes),
            )
        if process_limits.cpu_limit_seconds is not None:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (process_limits.cpu_limit_seconds, process_limits.cpu_limit_seconds),
            )

    return apply_limits


async def _run_ansible_command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    process_limits: AnsibleProcessLimits,
) -> int:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        preexec_fn=_build_process_limit_preexec(process_limits),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await process.wait()
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
