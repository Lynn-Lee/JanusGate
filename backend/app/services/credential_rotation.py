"""Credential rotation worker primitives."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models.account import Account, CredentialRotation
from app.services.automation_worker import JsonValue


@dataclass(frozen=True)
class CredentialRotationResult:
    secret_id: str


class CredentialRotator(Protocol):
    async def rotate(
        self, account: Account, rotation: CredentialRotation
    ) -> CredentialRotationResult: ...


class CredentialRotationWorker:
    def __init__(self, *, session: AsyncSession, rotator: CredentialRotator) -> None:
        self._session = session
        self._rotator = rotator

    async def run_due_rotations(self, *, now: datetime | None = None, limit: int = 20) -> int:
        if limit <= 0:
            return 0

        cutoff = _as_utc(now or datetime.now(UTC))
        result = await self._session.execute(_due_rotation_query(cutoff, limit))
        due_rotations = result.all()

        processed = 0
        for rotation, account in due_rotations:
            rotation.previous_secret_id = account.secret_id
            try:
                rotated = await self._rotator.rotate(account, rotation)
            except Exception as exc:
                rotation.status = "failed"
                rotation.error_code = _error_code(exc)
            else:
                account.secret_id = rotated.secret_id
                rotation.new_secret_id = rotated.secret_id
                rotation.error_code = None
                rotation.status = "completed"
            processed += 1

        if processed:
            await self._session.commit()
        return processed

    async def rollback_completed_rotation(self, *, rotation_id: int) -> bool:
        result = await self._session.execute(
            select(CredentialRotation, Account)
            .join(Account, CredentialRotation.account_id == Account.id)
            .where(CredentialRotation.id == rotation_id)
        )
        row = result.one_or_none()
        if row is None:
            return False

        rotation, account = row
        if (
            rotation.status != "completed"
            or rotation.previous_secret_id is None
            or account.secret_id != rotation.new_secret_id
        ):
            return False

        account.secret_id = rotation.previous_secret_id
        rotation.status = "rolled_back"
        await self._session.commit()
        return True


class CredentialRotateWorkerHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        rotator: CredentialRotator,
    ) -> None:
        self._session_factory = session_factory
        self._rotator = rotator

    async def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> None:
        del message_id
        account_id = _payload_int(payload, "account_id")
        reason = _payload_optional_str(payload, "reason")

        async with self._session_factory() as session:
            account = await _get_active_account(session, tenant_id=tenant_id, account_id=account_id)
            if account is None:
                raise ValueError("ACCOUNT_NOT_FOUND")

            rotation = CredentialRotation(
                tenant_id=account.tenant_id,
                account_id=account.id,
                status="scheduled",
                reason=reason,
                requested_by=requested_by,
                scheduled_at=None,
            )
            session.add(rotation)
            await session.flush()

            rotation.previous_secret_id = account.secret_id
            try:
                rotated = await self._rotator.rotate(account, rotation)
            except Exception as exc:
                rotation.status = "failed"
                rotation.error_code = _error_code(exc)
            else:
                account.secret_id = rotated.secret_id
                rotation.new_secret_id = rotated.secret_id
                rotation.error_code = None
                rotation.status = "completed"
            await session.commit()


def _due_rotation_query(
    now: datetime, limit: int
) -> Select[tuple[CredentialRotation, Account]]:
    return (
        select(CredentialRotation, Account)
        .join(Account, CredentialRotation.account_id == Account.id)
        .options(selectinload(Account.asset))
        .where(CredentialRotation.status == "scheduled")
        .where(
            or_(
                CredentialRotation.scheduled_at.is_(None),
                CredentialRotation.scheduled_at <= now,
            )
        )
        .order_by(CredentialRotation.id)
        .limit(limit)
    )


async def _get_active_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    account_id: int,
) -> Account | None:
    result = await session.execute(
        select(Account)
        .options(selectinload(Account.asset))
        .where(Account.id == account_id)
        .where(Account.tenant_id == tenant_id)
        .where(Account.status == "active")
    )
    return result.scalar_one_or_none()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _error_code(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message[:120]
    return exc.__class__.__name__[:120]


def _payload_int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value


def _payload_optional_str(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("AUTOMATION_JOB_PAYLOAD_INVALID")
    return value
