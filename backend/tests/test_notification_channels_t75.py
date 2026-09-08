"""#t75 notification channels, IM senders, subscriptions, and inbox."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.notification_channel import (
    CHANNEL_DINGTALK,
    CHANNEL_INBOX,
    CHANNEL_SLACK,
    InboxMessage,
    NotificationChannel,
    SystemMsgSubscription,
)
from app.models.webhook import NotificationDelivery, NotificationRule
from app.services.notification_channel_senders import (
    ChannelAwareNotificationSender,
    ImWebhookNotificationSender,
)
from app.services.notification_delivery_worker import NotificationDeliveryWorker
from app.services.notification_redaction import redact_payload


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    async def _db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    async def _user() -> dict[str, object]:
        return {
            "id": "1",
            "username": "alice",
            "tenant_id": "tenant-a",
            "permissions": ["admin"],
        }

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_read_db] = _db
    app.dependency_overrides[current_user] = _user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def test_redact_payload_masks_assignment_style_secrets() -> None:
    assert redact_payload({"note": "token=raw-secret please"}) == {
        "note": "token=[REDACTED] please"
    }
    assert redact_payload({"access_token": "abc"}) == {"access_token": "[REDACTED]"}


@pytest.mark.asyncio
async def test_notification_channel_api_rejects_http_and_stub_types(
    client: AsyncClient,
) -> None:
    bad_url = await client.post(
        "/api/v1/notification-channels/",
        json={
            "name": "slack-bad",
            "channel_type": "slack",
            "event_types": ["audit.event.created"],
            "config": {"webhook_url": "http://example.test/hooks"},
        },
    )
    assert bad_url.status_code == 400
    assert bad_url.json()["detail"] == "INVALID_CHANNEL_WEBHOOK_URL"

    stub = await client.post(
        "/api/v1/notification-channels/",
        json={
            "name": "sms-stub",
            "channel_type": "sms",
            "event_types": ["audit.event.created"],
            "config": {},
        },
    )
    assert stub.status_code == 400
    assert stub.json()["detail"] == "CHANNEL_TYPE_NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_channel_rule_enqueue_and_im_sender_posts(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    create = await client.post(
        "/api/v1/notification-channels/",
        json={
            "name": "ops-dingtalk",
            "channel_type": "dingtalk",
            "event_types": ["workflow.approved"],
            "config": {"webhook_url": "https://oapi.dingtalk.com/robot/send"},
        },
    )
    assert create.status_code == 201
    channel_id = create.json()["id"]

    rule = await client.post(
        "/api/v1/notification-rules/",
        json={
            "name": "approved-to-dingtalk",
            "event_types": ["workflow.approved"],
            "channel_id": channel_id,
        },
    )
    assert rule.status_code == 201
    rule_id = rule.json()["id"]
    assert rule.json()["channel_id"] == channel_id
    assert rule.json()["webhook_endpoint_id"] is None

    enqueue = await client.post(
        f"/api/v1/notification-rules/{rule_id}/deliveries",
        json={
            "event_type": "workflow.approved",
            "payload": {"request_id": "wr-1", "token": "should-hide"},
        },
    )
    assert enqueue.status_code == 202
    body = enqueue.json()
    assert body["channel_id"] == channel_id
    assert body["webhook_endpoint_id"] is None
    assert "payload" not in body
    assert "token" not in json.dumps(body)

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"errcode": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sender = ChannelAwareNotificationSender(http_client=http)
        worker = NotificationDeliveryWorker(session_factory=session_factory, sender=sender)
        result = await worker.run_due_once(now=datetime.now(UTC) + timedelta(seconds=1))

    assert result.delivered == 1
    assert len(seen) == 1
    assert str(seen[0].url) == "https://oapi.dingtalk.com/robot/send"
    posted = json.loads(seen[0].content)
    assert posted["msgtype"] == "text"
    assert "should-hide" not in posted["text"]["content"]
    assert "[REDACTED]" in posted["text"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_type", "url_key"),
    [
        (CHANNEL_SLACK, "text"),
        (CHANNEL_DINGTALK, "msgtype"),
    ],
)
async def test_im_sender_body_shapes(channel_type: str, url_key: str) -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200)

    channel = NotificationChannel(
        tenant_id="tenant-a",
        name="im",
        channel_type=channel_type,
        event_types_json='["evt"]',
        config_json=json.dumps({"webhook_url": "https://hooks.example.test/im"}),
        status="active",
    )
    delivery = NotificationDelivery(
        tenant_id="tenant-a",
        notification_rule_id=1,
        channel_id=1,
        event_type="evt",
        payload_json="{}",
        status="pending",
        attempts=0,
        next_attempt_at=datetime.now(UTC),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sender = ImWebhookNotificationSender(http_client=http)
        await sender.send_channel(channel=channel, delivery=delivery, payload={"ok": True})
    assert url_key in seen[0]


@pytest.mark.asyncio
async def test_system_msg_subscription_fans_out_redacted_inbox(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sub = await client.put(
        "/api/v1/system-msg-subscriptions/me",
        json={
            "event_types": ["workflow.approved"],
            "channel_types": [CHANNEL_INBOX],
            "status": "active",
        },
    )
    assert sub.status_code == 200
    assert sub.json()["user_id"] == "1"

    channel = await client.post(
        "/api/v1/notification-channels/",
        json={
            "name": "slack-ops",
            "channel_type": "slack",
            "event_types": ["workflow.approved"],
            "config": {"webhook_url": "https://hooks.slack.com/services/x"},
        },
    )
    rule = await client.post(
        "/api/v1/notification-rules/",
        json={
            "name": "approved-to-slack",
            "event_types": ["workflow.approved"],
            "channel_id": channel.json()["id"],
        },
    )
    enqueue = await client.post(
        f"/api/v1/notification-rules/{rule.json()['id']}/deliveries",
        json={
            "event_type": "workflow.approved",
            "payload": {"title": "已批准", "password": "super-secret"},
        },
    )
    assert enqueue.status_code == 202

    inbox = await client.get("/api/v1/inbox-messages/")
    assert inbox.status_code == 200
    items = inbox.json()["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "workflow.approved"
    assert items[0]["body"]["password"] == "[REDACTED]"
    assert "super-secret" not in json.dumps(items[0])

    marked = await client.post(f"/api/v1/inbox-messages/{items[0]['id']}/read")
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None

    async with session_factory() as session:
        messages = (await session.execute(select(InboxMessage))).scalars().all()
        subscriptions = (await session.execute(select(SystemMsgSubscription))).scalars().all()
    assert len(messages) == 1
    assert len(subscriptions) == 1
    assert "super-secret" not in messages[0].body_json


@pytest.mark.asyncio
async def test_inbox_channel_delivery_writes_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        channel = NotificationChannel(
            tenant_id="tenant-a",
            name="inbox-default",
            channel_type=CHANNEL_INBOX,
            event_types_json=json.dumps(["session.closed"]),
            config_json=json.dumps({"user_id": "42"}),
            status="active",
        )
        session.add(channel)
        await session.flush()
        rule = NotificationRule(
            tenant_id="tenant-a",
            name="session-closed-inbox",
            event_types_json=json.dumps(["session.closed"]),
            channel_id=channel.id,
            status="active",
        )
        session.add(rule)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id="tenant-a",
            notification_rule_id=rule.id,
            channel_id=channel.id,
            event_type="session.closed",
            payload_json=json.dumps({"title": "会话已关闭", "session_id": "s-1"}),
            status="pending",
            attempts=0,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    sender = ChannelAwareNotificationSender()
    worker = NotificationDeliveryWorker(session_factory=session_factory, sender=sender)
    result = await worker.run_due_once(now=now)
    assert result.delivered == 1

    async with session_factory() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)
        messages = (await session.execute(select(InboxMessage))).scalars().all()
    assert delivery is not None
    assert delivery.status == "delivered"
    assert len(messages) == 1
    assert messages[0].user_id == "42"
    assert messages[0].event_type == "session.closed"
