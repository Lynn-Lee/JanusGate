"""Phase 4 notification delivery worker tests."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.webhook import NotificationDelivery, NotificationRule, WebhookEndpoint
from app.services.notification_delivery_worker import (
    NotificationDeliverySender,
    NotificationDeliveryWorker,
)


class RecordingSender(NotificationDeliverySender):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[dict[str, object]] = []

    async def send(
        self,
        *,
        endpoint: WebhookEndpoint,
        delivery: NotificationDelivery,
        payload: dict[str, object],
    ) -> None:
        self.requests.append(
            {"endpoint_url": endpoint.url, "event_type": delivery.event_type, "payload": payload}
        )
        if self.fail:
            raise RuntimeError("webhook sink rejected delivery")


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    attempts: int = 0,
    payload: dict[str, object] | None = None,
    due_at: datetime | None = None,
) -> int:
    now = datetime(2026, 7, 4, 5, 40, tzinfo=UTC)
    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="security-siem",
            url="https://siem.example.test/janusgate",
            event_types_json=json.dumps(["audit.event.created"]),
            status="active",
        )
        session.add(endpoint)
        await session.flush()
        rule = NotificationRule(
            tenant_id="tenant-a",
            name="audit-created-to-siem",
            event_types_json=json.dumps(["audit.event.created"]),
            webhook_endpoint_id=endpoint.id,
            status="active",
        )
        session.add(rule)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id="tenant-a",
            notification_rule_id=rule.id,
            webhook_endpoint_id=endpoint.id,
            event_type="audit.event.created",
            payload_json=json.dumps(payload or {"audit_event_id": "evt-1"}),
            status="pending",
            attempts=attempts,
            next_attempt_at=due_at or now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()
        return delivery.id


@pytest.mark.asyncio
async def test_notification_delivery_worker_marks_success_delivered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = await seed_delivery(
        session_factory,
        payload={"audit_event_id": "evt-1", "token": "[REDACTED]"},
    )
    sender = RecordingSender()

    worker = NotificationDeliveryWorker(session_factory=session_factory, sender=sender)
    result = await worker.run_due_once(now=datetime(2026, 7, 4, 5, 40, tzinfo=UTC))

    async with session_factory() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)

    assert result.processed == 1
    assert result.delivered == 1
    assert result.failed == 0
    assert result.dead_lettered == 0
    assert delivery is not None
    assert delivery.status == "delivered"
    assert delivery.attempts == 1
    assert delivery.last_error is None
    assert sender.requests == [
        {
            "endpoint_url": "https://siem.example.test/janusgate",
            "event_type": "audit.event.created",
            "payload": {"audit_event_id": "evt-1", "token": "[REDACTED]"},
        }
    ]


@pytest.mark.asyncio
async def test_notification_delivery_worker_retries_then_dead_letters(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = await seed_delivery(session_factory, attempts=2)
    sender = RecordingSender(fail=True)
    now = datetime(2026, 7, 4, 5, 40, tzinfo=UTC)

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        sender=sender,
        max_attempts=3,
        retry_delay=timedelta(minutes=5),
    )
    result = await worker.run_due_once(now=now)

    async with session_factory() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)

    assert result.processed == 1
    assert result.delivered == 0
    assert result.failed == 0
    assert result.dead_lettered == 1
    assert delivery is not None
    assert delivery.status == "dead_letter"
    assert delivery.attempts == 3
    assert delivery.next_attempt_at.replace(tzinfo=UTC) == now + timedelta(minutes=5)
    assert delivery.last_error == "webhook sink rejected delivery"
