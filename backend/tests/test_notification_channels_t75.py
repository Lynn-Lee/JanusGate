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
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_channels import ChannelNotificationSender, SmtpNotificationSender
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


def install_user(*, tenant_id: str, permissions: list[str], user_id: str = "user-1") -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": user_id,
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


def _endpoint(
    *,
    channel_type: str,
    url: str,
    target: str = "",
    credential: str = "",
) -> WebhookEndpoint:
    return WebhookEndpoint(
        tenant_id="tenant-a",
        name=f"{channel_type}-channel",
        url=url,
        event_types_json=json.dumps(["audit.event.created"]),
        channel_type=channel_type,
        target=target,
        credential_encrypted=encrypt_field(credential) if credential else "",
        status="active",
    )


def _delivery(*, payload: dict[str, object], recipient_user_id: str = "") -> NotificationDelivery:
    return NotificationDelivery(
        tenant_id="tenant-a",
        notification_rule_id=None,
        webhook_endpoint_id=1,
        recipient_user_id=recipient_user_id,
        event_type="audit.event.created",
        payload_json=json.dumps(payload),
        status="pending",
        attempts=0,
        next_attempt_at=datetime(2026, 9, 10, 2, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_webhook_endpoint_api_rejects_im_channel_on_unallowed_host(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "evil-dingtalk",
                "channel_type": "dingtalk",
                "url": "https://evil.example.test/robot/send?access_token=secret-token",
                "event_types": ["audit.event.created"],
            },
        )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CHANNEL_HOST"
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_dingtalk_channel_create_strips_access_token_from_response(
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
                "url": "https://oapi.dingtalk.com/robot/send?access_token=secret-token",
                "event_types": ["audit.event.created"],
                "credential": "dingtalk-sign-secret",
            },
        )
    assert response.status_code == 201
    body = response.json()
    assert body["channel_type"] == "dingtalk"
    assert body["url"] == "https://oapi.dingtalk.com/robot/send"
    assert body["credential_configured"] is True
    assert "secret-token" not in response.text
    assert "dingtalk-sign-secret" not in response.text


@pytest.mark.asyncio
async def test_system_message_subscription_fanout_is_tenant_scoped_and_redacts_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="42")
        inbox = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-inbox",
                "channel_type": "inbox",
                "url": "inbox://local",
                "event_types": ["audit.event.created"],
            },
        )
        assert inbox.status_code == 201
        endpoint_id = inbox.json()["id"]
        created = client.post(
            "/api/v1/notification-subscriptions/",
            json={
                "user_id": "42",
                "webhook_endpoint_id": endpoint_id,
                "event_types": ["audit.event.created"],
            },
        )
        assert created.status_code == 201
        install_user(tenant_id="tenant-b", permissions=["admin"])
        listed_b = client.get("/api/v1/notification-subscriptions/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="42")
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "audit.event.created",
                "payload": {"audit_event_id": "evt-1", "token": "super-secret"},
            },
        )
        deliveries = client.get("/api/v1/notification-deliveries/")

    assert listed_b.status_code == 200
    assert listed_b.json() == {"items": [], "total": 0}
    assert fanout.status_code == 202
    assert fanout.json()["enqueued"] == 1
    listed = deliveries.json()["items"]
    assert len(listed) == 1
    assert listed[0]["recipient_user_id"] == "42"
    assert listed[0]["notification_rule_id"] is None
    assert "super-secret" not in deliveries.text
    assert "payload" not in listed[0]


@pytest.mark.asyncio
async def test_channel_sender_posts_dingtalk_text_and_hides_errors(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, text="downstream rejected password=secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelNotificationSender(http_client=client, session_factory=session_factory)
        with pytest.raises(RuntimeError) as exc_info:
            await sender.send(
                endpoint=_endpoint(
                    channel_type="dingtalk",
                    url="https://oapi.dingtalk.com/robot/send?access_token=secret-token",
                    credential="robot-secret",
                ),
                delivery=_delivery(payload={"audit_event_id": "evt-1"}),
                payload={"audit_event_id": "evt-1", "token": "[REDACTED]"},
            )

    assert str(exc_info.value) == "channel delivery failed with status 503"
    assert "password" not in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["msgtype"] == "text"
    assert "audit.event.created" in body["text"]["content"]
    assert "[REDACTED]" in body["text"]["content"]


@pytest.mark.asyncio
async def test_channel_sender_rejects_slack_host_outside_allowlist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with httpx.AsyncClient() as client:
        sender = ChannelNotificationSender(http_client=client, session_factory=session_factory)
        with pytest.raises(RuntimeError) as exc_info:
            await sender.send(
                endpoint=_endpoint(
                    channel_type="slack",
                    url="https://evil.example.test/services/secret-token",
                ),
                delivery=_delivery(payload={"audit_event_id": "evt-1"}),
                payload={"audit_event_id": "evt-1"},
            )
    assert str(exc_info.value) == "channel host is not allowed"
    assert "secret-token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_sms_channel_sends_bearer_and_redacted_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelNotificationSender(http_client=client, session_factory=session_factory)
        await sender.send(
            endpoint=_endpoint(
                channel_type="sms",
                url="https://sms.example.test/send",
                target="+15551212",
                credential="sms-api-key",
            ),
            delivery=_delivery(payload={"audit_event_id": "evt-1"}),
            payload={"audit_event_id": "evt-1", "token": "[REDACTED]"},
        )

    assert seen[0].headers["authorization"] == "Bearer sms-api-key"
    assert json.loads(seen[0].content)["payload"]["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_email_channel_requires_tls_smtp_and_does_not_log_password(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class RecordingSmtp(SmtpNotificationSender):
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def send_message(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    smtp = RecordingSmtp()
    sender = ChannelNotificationSender(session_factory=session_factory, smtp_sender=smtp)
    await sender.send(
        endpoint=_endpoint(
            channel_type="email",
            url="smtps://alerts@mail.example.test:465",
            target="ops@example.test",
            credential="smtp-password",
        ),
        delivery=_delivery(payload={"audit_event_id": "evt-1"}),
        payload={"audit_event_id": "evt-1", "token": "[REDACTED]"},
    )
    assert smtp.calls[0]["use_ssl"] is True
    assert smtp.calls[0]["password"] == "smtp-password"
    assert smtp.calls[0]["mail_to"] == "ops@example.test"
    assert "[REDACTED]" in str(smtp.calls[0]["body"])


@pytest.mark.asyncio
async def test_inbox_channel_writes_in_app_message_and_lists_only_current_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    sender = ChannelNotificationSender(session_factory=session_factory)
    await sender.send(
        endpoint=_endpoint(channel_type="inbox", url="inbox://local", target="42"),
        delivery=_delivery(payload={"audit_event_id": "evt-1"}, recipient_user_id="42"),
        payload={"audit_event_id": "evt-1", "token": "[REDACTED]"},
    )

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="99")
        other = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-a", permissions=[], user_id="42")
        mine = client.get("/api/v1/in-app-messages/")
        message_id = mine.json()["items"][0]["id"]
        read = client.post(f"/api/v1/in-app-messages/{message_id}/read")

    assert other.json() == {"items": [], "total": 0}
    assert mine.status_code == 200
    item = mine.json()["items"][0]
    assert item["payload"] == {"audit_event_id": "evt-1", "token": "[REDACTED]"}
    assert read.json()["read_at"] is not None


@pytest.mark.asyncio
async def test_worker_delivers_subscription_inbox_without_notification_rule(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
    async with session_factory() as session:
        endpoint = _endpoint(channel_type="inbox", url="inbox://local")
        session.add(endpoint)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id="tenant-a",
            notification_rule_id=None,
            webhook_endpoint_id=endpoint.id,
            recipient_user_id="42",
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
    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, delivery_id)
        messages = (await session.execute(select(InAppMessage))).scalars().all()

    assert result.delivered == 1
    assert stored is not None
    assert stored.status == "delivered"
    assert len(messages) == 1
