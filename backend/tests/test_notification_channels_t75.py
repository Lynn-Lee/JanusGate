"""#t75 通知渠道：官方 host、凭据剥离、脱敏扇出、站内信与 dead-letter 契约。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_channels import (
    CHANNEL_DINGTALK,
    CHANNEL_SLACK,
    ChannelNotificationSender,
    ChannelValidationError,
    reconstruct_delivery_url,
    redact_notification_payload,
    sanitize_channel_target,
)
from app.services.notification_delivery_worker import NotificationDeliveryWorker


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


def install_user(*, tenant_id: str, permissions: list[str], user_id: str = "1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_sanitize_strips_dingtalk_token_and_rejects_unofficial_host() -> None:
    target = sanitize_channel_target(
        channel_type=CHANNEL_DINGTALK,
        url="https://oapi.dingtalk.com/robot/send?access_token=robot-secret",
    )
    assert target.public_url == "https://oapi.dingtalk.com/robot/send"
    assert "robot-secret" not in target.public_url
    assert target.credential == "robot-secret"

    with pytest.raises(ChannelValidationError) as exc_info:
        sanitize_channel_target(
            channel_type=CHANNEL_DINGTALK,
            url="https://evil.example.test/robot/send?access_token=robot-secret",
        )
    assert exc_info.value.code == "CHANNEL_HOST_NOT_ALLOWED"


def test_sanitize_keeps_webhook_query_but_rejects_userinfo() -> None:
    target = sanitize_channel_target(
        channel_type="webhook",
        url="https://siem.example.test/janusgate?source=core",
    )
    assert target.public_url == "https://siem.example.test/janusgate?source=core"
    with pytest.raises(ChannelValidationError) as exc_info:
        sanitize_channel_target(
            channel_type="webhook",
            url="https://user:pass@siem.example.test/janusgate",
        )
    assert exc_info.value.code == "INVALID_WEBHOOK_URL"


def test_sanitize_slack_path_secret_and_redact_payload() -> None:
    target = sanitize_channel_target(
        channel_type=CHANNEL_SLACK,
        url="https://hooks.slack.com/services/T000/B000/XXXX",
    )
    assert target.public_url == "https://hooks.slack.com/services"
    assert target.credential == "T000/B000/XXXX"
    redacted = redact_notification_payload(
        {"audit_event_id": "evt-1", "access_token": "raw", "note": "password=super"}
    )
    assert redacted["access_token"] == "[REDACTED]"
    assert "super" not in str(redacted["note"])


@pytest.mark.asyncio
async def test_channel_api_strips_im_secret_from_response(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-dingtalk",
                "channel_type": "dingtalk",
                "url": "https://oapi.dingtalk.com/robot/send?access_token=robot-secret",
                "event_types": ["audit.event.created"],
            },
        )
        listed = client.get("/api/v1/webhook-endpoints/")

    assert created.status_code == 201
    body = created.json()
    assert body["channel_type"] == "dingtalk"
    assert body["url"] == "https://oapi.dingtalk.com/robot/send"
    assert body["credential_configured"] is True
    assert "robot-secret" not in created.text
    assert "credential_encrypted" not in body
    assert listed.json()["items"][0]["url"] == body["url"]


@pytest.mark.asyncio
async def test_rule_rejects_inbox_channel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        inbox = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-inbox",
                "channel_type": "inbox",
                "event_types": ["audit.event.created"],
            },
        )
        assert inbox.status_code == 201
        response = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "inbox-rule",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox.json()["id"],
            },
        )
    assert response.status_code == 400
    assert response.json()["code"] == "INBOX_CHANNEL_REQUIRES_SUBSCRIPTION"


@pytest.mark.asyncio
async def test_subscription_requires_inbox_recipient(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        inbox = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-inbox",
                "channel_type": "inbox",
                "event_types": ["audit.event.created"],
            },
        )
        missing = client.post(
            "/api/v1/notification-subscriptions/",
            json={
                "name": "inbox-sub",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox.json()["id"],
            },
        )
        created = client.post(
            "/api/v1/notification-subscriptions/",
            json={
                "name": "inbox-sub",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox.json()["id"],
                "recipient_user_id": "1",
            },
        )
    assert missing.status_code == 400
    assert missing.json()["code"] == "INBOX_RECIPIENT_REQUIRED"
    assert created.status_code == 201
    assert created.json()["recipient_user_id"] == "1"


@pytest.mark.asyncio
async def test_fanout_redacts_payload_and_isolates_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        endpoint = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
        rule = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "audit-to-siem",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": endpoint.json()["id"],
            },
        )
        assert rule.status_code == 201
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "audit.event.created",
                "payload": {"audit_event_id": "evt-1", "token": "raw-secret"},
            },
        )
        deliveries = client.get("/api/v1/notification-deliveries/")
        install_user(tenant_id="tenant-b", permissions=["admin"])
        other = client.get("/api/v1/notification-deliveries/")

    assert fanout.status_code == 202
    assert fanout.json()["created"] == 1
    assert "raw-secret" not in fanout.text
    listed = deliveries.json()["items"][0]
    assert listed["event_type"] == "audit.event.created"
    assert "payload" not in listed
    assert "raw-secret" not in deliveries.text
    assert other.json() == {"items": [], "total": 0}

    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, listed["id"])
    assert stored is not None
    assert json.loads(stored.payload_json)["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_email_gateway_uses_bearer_and_hides_downstream_body(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, text="smtp password=leak")

    endpoint = WebhookEndpoint(
        tenant_id="tenant-a",
        name="email-gw",
        url="https://notify.example.test/email",
        channel_type="email",
        event_types_json=json.dumps(["audit.event.created"]),
        credential_encrypted=__import__("app.core.security", fromlist=["encrypt_field"]).encrypt_field(
            "gateway-bearer"
        ),
        status="active",
    )
    delivery = NotificationDelivery(
        tenant_id="tenant-a",
        notification_rule_id=None,
        webhook_endpoint_id=1,
        event_type="audit.event.created",
        payload_json="{}",
        status="pending",
        attempts=0,
        next_attempt_at=datetime.now(UTC),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelNotificationSender(http_client=client)
        with pytest.raises(RuntimeError) as exc_info:
            await sender.send(
                endpoint=endpoint,
                delivery=delivery,
                payload={"audit_event_id": "evt-1"},
            )
    assert str(exc_info.value) == "channel delivery failed with status 503"
    assert "password" not in str(exc_info.value)
    assert "leak" not in str(exc_info.value)
    assert seen[0].headers["authorization"] == "Bearer gateway-bearer"
    assert "gateway-bearer" not in reconstruct_delivery_url(endpoint)


@pytest.mark.asyncio
async def test_inbox_worker_writes_current_user_message_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="ops-inbox",
            url="",
            channel_type="inbox",
            event_types_json=json.dumps(["audit.event.created"]),
            status="active",
        )
        session.add(endpoint)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id="tenant-a",
            notification_rule_id=None,
            webhook_endpoint_id=endpoint.id,
            recipient_user_id="1",
            event_type="audit.event.created",
            payload_json=json.dumps({"audit_event_id": "evt-1"}),
            status="pending",
            attempts=0,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        sender=ChannelNotificationSender(session_factory=session_factory),
    )
    result = await worker.run_due_once(now=now)
    assert result.delivered == 1

    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[], user_id="1")
        mine = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="2")
        other = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-b", permissions=["admin"], user_id="1")
        cross = client.get("/api/v1/in-app-messages/")

    assert mine.status_code == 200
    assert mine.json()["total"] == 1
    assert mine.json()["items"][0]["event_type"] == "audit.event.created"
    assert mine.json()["items"][0]["body"]["audit_event_id"] == "evt-1"
    assert other.json() == {"items": [], "total": 0}
    assert cross.json() == {"items": [], "total": 0}

    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, delivery_id)
        inbox = (await session.execute(__import__("sqlalchemy", fromlist=["select"]).select(InAppMessage))).scalars().all()
    assert stored is not None
    assert stored.status == "delivered"
    assert len(inbox) == 1
    assert inbox[0].recipient_user_id == "1"
