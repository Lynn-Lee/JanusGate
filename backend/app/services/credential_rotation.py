"""Credential rotation worker primitives."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, CredentialRotation


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


def _due_rotation_query(
    now: datetime, limit: int
) -> Select[tuple[CredentialRotation, Account]]:
    return (
        select(CredentialRotation, Account)
        .join(Account, CredentialRotation.account_id == Account.id)
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _error_code(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message[:120]
    return exc.__class__.__name__[:120]
