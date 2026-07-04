"""Ansible playbook automation worker handler."""
from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.asset import Asset
from app.services.automation_worker import JsonValue


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


class AnsiblePlaybookRunner(Protocol):
    def run(self, playbook: AnsiblePlaybookRun) -> Awaitable[None]: ...


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
        del message_id
        playbook_name = _payload_str(payload, "playbook_name")
        target_asset_ids = _payload_int_list(payload, "target_asset_ids")
        check_mode = _payload_bool(payload, "check_mode")

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

        await self._runner.run(
            AnsiblePlaybookRun(
                tenant_id=tenant_id,
                requested_by=requested_by,
                playbook_name=playbook_name,
                check_mode=check_mode,
                targets=targets,
            )
        )


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
