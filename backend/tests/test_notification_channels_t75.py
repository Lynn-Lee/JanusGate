"""Phase 6 #t75 notification channel, subscription and inbox tests."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.security import encrypt_field
from app.main import app
from app.models.webhook import (
    InAppMessage,
    NotificationDelivery,
    NotificationRule,
    WebhookEndpoint,
)
from app.services.notification_channels import (
    ChannelConfigError,
    ChannelNotificationSender,
    sanitize_channel,
)
from app.services.notification_delivery_worker import NotificationDeliveryWorker
from app.services.notification_redact import payload_json, payload_summary, redact_payload


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def install_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_read_db] = override_db


def install_user(
    *,
    tenant_id: str,
    permissions: list[str],
    user_id: str = "user-1",
) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


def test_sanitize_channel_strips_im_token_and_pins_official_host() -> None:
    ding = sanitize_channel(
        channel_type="dingtalk",
        url="https://oapi.dingtalk.com/robot/send?access_token=robot-secret",
    )
    assert ding.url == "https://oapi.dingtalk.com/robot/send"
    assert ding.credential == "robot-secret"

    with pytest.raises(ChannelConfigError, match="CHANNEL_HOST_NOT_ALLOWED"):
        sanitize_channel(
            channel_type="dingtalk",
            url="https://evil.example.test/robot/send?access_token=robot-secret",
        )
    with pytest.raises(ChannelConfigError, match="INVALID_WEBHOOK_URL"):
        sanitize_channel(channel_type="webhook", url="http://siem.example.test/hook")
    with pytest.raises(ChannelConfigError, match="INBOX_RECIPIENT_REQUIRED"):
        sanitize_channel(channel_type="inbox", url="")
    webhook = sanitize_channel(
        channel_type="webhook",
        url="https://siem.example.test/hook?source=janusgate",
    )
    assert webhook.url == "https://siem.example.test/hook?source=janusgate"
    with pytest.raises(ChannelConfigError, match="CHANNEL_CREDENTIAL_REQUIRED"):
        sanitize_channel(
            channel_type="email",
            url="https://mail.example.test/send",
            recipient="ops@example.test",
        )
    email = sanitize_channel(
        channel_type="email",
        url="https://mail.example.test/send",
        credential="gateway-token",
        recipient="ops@example.test",
    )
    assert email.credential == "gateway-token"
    assert email.recipient == "ops@example.test"


def test_redact_payload_masks_sensitive_keys_and_assignment_text() -> None:
    redacted = redact_payload(
        {"audit_event_id": "evt-1", "token": "raw-secret", "note": "password=hunter2"}
    )
    assert redacted == {
        "audit_event_id": "evt-1",
        "token": "[REDACTED]",
        "note": "password=[REDACTED]",
    }
    assert "raw-secret" not in payload_json({"token": "raw-secret"})
    assert "hunter2" not in payload_summary({"message": "token=abc123"})


@pytest.mark.asyncio
async def test_webhook_endpoint_api_creates_dingtalk_without_returning_credential(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-dingtalk",
                "channel_type": "dingtalk",
                "url": "https://oapi.dingtalk.com/robot/send?access_token=robot-secret",
                "event_types": ["workflow.request.approved"],
            },
        )
        listed = client.get("/api/v1/webhook-endpoints/")

    assert response.status_code == 201
    created = response.json()
    assert created["channel_type"] == "dingtalk"
    assert created["url"] == "https://oapi.dingtalk.com/robot/send"
    assert created["credential_configured"] is True
    assert "credential" not in created
    assert "robot-secret" not in json.dumps(created)
    assert "access_token" not in created["url"]
    assert listed.json()["items"][0]["url"] == created["url"]

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["notifications:write", "notifications:read"])
        alias = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-webhook",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
            },
        )
    assert alias.status_code == 201


@pytest.mark.asyncio
async def test_webhook_endpoint_api_rejects_non_official_im_host(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "evil-feishu",
                "channel_type": "feishu",
                "url": "https://evil.example.test/open-apis/bot/v2/hook/secret",
                "event_types": ["workflow.request.approved"],
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "CHANNEL_HOST_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_channel_sender_posts_redacted_im_body_and_fail_closes_without_leak() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"errcode": 0})

    endpoint = WebhookEndpoint(
        tenant_id="tenant-a",
        name="ops-dingtalk",
        url="https://oapi.dingtalk.com/robot/send",
        event_types_json=json.dumps(["workflow.request.approved"]),
        channel_type="dingtalk",
        credential_encrypted=encrypt_field("robot-secret"),
        status="active",
    )
    delivery = NotificationDelivery(
        tenant_id="tenant-a",
        notification_rule_id=1,
        webhook_endpoint_id=1,
        event_type="workflow.request.approved",
        payload_json=payload_json({"token": "raw-secret", "summary": "审批通过"}),
        status="pending",
        attempts=0,
        next_attempt_at=datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelNotificationSender(http_client=client)
        await sender.send(
            endpoint=endpoint,
            delivery=delivery,
            payload={"token": "[REDACTED]", "summary": "审批通过"},
        )

    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://oapi.dingtalk.com/robot/send?access_token=robot-secret"
    body = json.loads(request.content)
    assert body == {"msgtype": "text", "text": {"content": "workflow.request.approved: 审批通过"}}
    assert "raw-secret" not in json.dumps(body)
    assert "robot-secret" not in json.dumps(body)


@pytest.mark.asyncio
async def test_channel_sender_http_error_does_not_leak_payload_or_downstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="downstream rejected password=secret token=abc")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelNotificationSender(http_client=client)
        with pytest.raises(RuntimeError) as exc_info:
            await sender.send(
                endpoint=WebhookEndpoint(
                    tenant_id="tenant-a",
                    name="ops-slack",
                    url="https://hooks.slack.com/services",
                    event_types_json=json.dumps(["audit.event.created"]),
                    channel_type="slack",
                    credential_encrypted=encrypt_field("T000/B000/secret"),
                    status="active",
                ),
                delivery=NotificationDelivery(
                    tenant_id="tenant-a",
                    notification_rule_id=1,
                    webhook_endpoint_id=1,
                    event_type="audit.event.created",
                    payload_json=payload_json({"password": "hunter2"}),
                    status="pending",
                    attempts=0,
                    next_attempt_at=datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
                ),
                payload={"password": "[REDACTED]"},
            )

    message = str(exc_info.value)
    assert message == "slack delivery failed with status 503"
    assert "hunter2" not in message
    assert "password" not in message
    assert "T000" not in message


@pytest.mark.asyncio
async def test_inbox_delivery_writes_redacted_message_and_worker_marks_delivered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)
    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="inbox-alice",
            url="inbox://local",
            event_types_json=json.dumps(["workflow.request.approved"]),
            channel_type="inbox",
            recipient="user-1",
            status="active",
        )
        session.add(endpoint)
        await session.flush()
        rule = NotificationRule(
            tenant_id="tenant-a",
            name="approved-to-inbox",
            event_types_json=json.dumps(["workflow.request.approved"]),
            webhook_endpoint_id=endpoint.id,
            status="active",
        )
        session.add(rule)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id="tenant-a",
            notification_rule_id=rule.id,
            webhook_endpoint_id=endpoint.id,
            event_type="workflow.request.approved",
            payload_json=payload_json({"token": "raw-secret", "summary": "审批通过"}),
            status="pending",
            attempts=0,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        sender=ChannelNotificationSender(),
    )
    result = await worker.run_due_once(now=now)

    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, delivery_id)
        messages = (await session.execute(select(InAppMessage))).scalars().all()

    assert result.delivered == 1
    assert stored is not None
    assert stored.status == "delivered"
    assert len(messages) == 1
    assert messages[0].user_id == "user-1"
    assert messages[0].body == "审批通过"
    assert "raw-secret" not in messages[0].body


@pytest.mark.asyncio
async def test_subscription_fanout_and_inbox_are_tenant_and_user_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="user-1")
        endpoint = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "inbox-alice",
                "channel_type": "inbox",
                "recipient": "user-1",
                "event_types": ["workflow.request.approved"],
            },
        )
        endpoint_id = endpoint.json()["id"]
        sub = client.post(
            "/api/v1/notification-subscriptions/",
            json={
                "event_type": "workflow.request.approved",
                "webhook_endpoint_id": endpoint_id,
                "user_id": "user-1",
            },
        )
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "workflow.request.approved",
                "subject_user_id": "user-1",
                "payload": {"token": "raw-secret", "summary": "审批通过"},
            },
        )
        listed = client.get("/api/v1/notification-deliveries/")

        worker = NotificationDeliveryWorker(
            session_factory=session_factory,
            sender=ChannelNotificationSender(),
        )

    result = await worker.run_due_once(now=datetime.now(UTC))

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="user-1")
        inbox = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="user-2")
        other_inbox = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-b", permissions=["admin"], user_id="user-1")
        other_tenant = client.get("/api/v1/notification-subscriptions/")

    assert sub.status_code == 201
    assert fanout.status_code == 202
    assert fanout.json()["enqueued"] == 1
    assert listed.json()["total"] == 1
    assert "payload" not in listed.json()["items"][0]
    assert result.delivered == 1
    assert inbox.json()["total"] == 1
    assert inbox.json()["items"][0]["body"] == "审批通过"
    assert "raw-secret" not in json.dumps(inbox.json())
    assert other_inbox.json() == {"items": [], "total": 0}
    assert other_tenant.json() == {"items": [], "total": 0}
