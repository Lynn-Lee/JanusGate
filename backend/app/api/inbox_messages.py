"""站内信读取 API。当前用户只能看到自己租户内发给自己的消息。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhook_schemas import InAppMessageListResponse, InAppMessageResponse
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.models.webhook import InAppMessage

router = APIRouter(prefix="/inbox-messages", tags=["Inbox Messages"])


@router.get("/", response_model=InAppMessageListResponse)
async def list_inbox_messages(
    db: AsyncSession = Depends(get_read_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageListResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id"))
    result = await db.execute(
        select(InAppMessage)
        .where(InAppMessage.tenant_id == tenant_id, InAppMessage.user_id == user_id)
        .order_by(InAppMessage.id.desc())
    )
    messages = result.scalars().all()
    items = [_message_response(item) for item in messages]
    return InAppMessageListResponse(items=items, total=len(items))


@router.post("/{message_id}/read", response_model=InAppMessageResponse)
async def mark_inbox_message_read(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> InAppMessageResponse:
    tenant_id = str(user.get("tenant_id") or "default")
    user_id = str(user.get("id"))
    result = await db.execute(
        select(InAppMessage).where(
            InAppMessage.id == message_id,
            InAppMessage.tenant_id == tenant_id,
            InAppMessage.user_id == user_id,
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="INBOX_MESSAGE_NOT_FOUND")
    if message.read_at is None:
        message.read_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(message)
    return _message_response(message)


def _message_response(message: InAppMessage) -> InAppMessageResponse:
    return InAppMessageResponse(
        id=message.id,
        tenant_id=message.tenant_id,
        user_id=message.user_id,
        delivery_id=message.delivery_id,
        event_type=message.event_type,
        title=message.title,
        body=message.body,
        read_at=_as_utc(message.read_at),
        created_at=_as_utc(message.created_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
