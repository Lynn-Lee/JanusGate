"""#t75 通知渠道：官方 host、URL 去密、扇出、站内信与 dead-letter 契约。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.security import decrypt_field, encrypt_field
from app.main import app
from app.models.webhook import InAppMessage, NotificationDelivery, WebhookEndpoint
from app.services.notification_channels import (
    ChannelConfigError,
    ChannelNotificationSender,
    SmtpMailSender,
    sanitize_channel,
)
from app.services.notification_delivery_worker import NotificationDeliveryWorker
from app.services.notification_redact import redact_payload


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


def test_sanitize_strips_dingtalk_access_token_from_url() -> None:
    sanitized = sanitize_channel(
        channel_type="dingtalk",
        url="https://oapi.dingtalk.com/robot/send?access_token=super-secret-token",
    )
    assert sanitized.url == "https://oapi.dingtalk.com/robot/send"
    assert sanitized.credential == "super-secret-token"


def test_sanitize_rejects_non_official_im_host() -> None:
    with pytest.raises(ChannelConfigError, match="CHANNEL_HOST_NOT_ALLOWED"):
        sanitize_channel(
            channel_type="slack",
            url="https://evil.example.test/services/T00/B00/xxx",
            credential="T00/B00/xxx",
        )


def test_sanitize_rejects_webhook_query() -> None:
    with pytest.raises(ChannelConfigError, match="CHANNEL_URL_QUERY_FORBIDDEN"):
        sanitize_channel(
            channel_type="webhook",
            url="https://siem.example.test/hook?token=abc",
        )


def test_redact_payload_masks_assignment_fragments() -> None:
    assert redact_payload({"note": "password=plain-secret"}) == {"note": "password=[REDACTED]"}


@pytest.mark.asyncio
async def test_dingtalk_channel_create_does_not_echo_token(
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
                "url": "https://oapi.dingtalk.com/robot/send?access_token=super-secret-token",
                "event_types": ["audit.event.created"],
            },
        )
        listed = client.get("/api/v1/webhook-endpoints/")

    assert created.status_code == 201
    body = created.json()
    assert body["channel_type"] == "dingtalk"
    assert body["url"] == "https://oapi.dingtalk.com/robot/send"
    assert "super-secret-token" not in json.dumps(body)
    assert body["credential_configured"] is True
    assert "access_token" not in listed.json()["items"][0]["url"]

    async with session_factory() as session:
        endpoint = await session.get(WebhookEndpoint, body["id"])
    assert endpoint is not None
    assert decrypt_field(endpoint.credential_encrypted or "") == "super-secret-token"
    assert "super-secret-token" not in (endpoint.url or "")


@pytest.mark.asyncio
async def test_inbox_subscription_fanout_and_worker_writes_in_app_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="42")
        channel = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "ops-inbox",
                "channel_type": "inbox",
                "url": "",
                "recipient": "42",
                "event_types": ["audit.event.created"],
            },
        )
        assert channel.status_code == 201
        endpoint_id = channel.json()["id"]
        subscribed = client.post(
            "/api/v1/notification-subscriptions/",
            json={"event_type": "audit.event.created", "webhook_endpoint_id": endpoint_id},
        )
        assert subscribed.status_code == 201
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "audit.event.created",
                "payload": {"message": "host key changed", "token": "should-hide"},
            },
        )
        assert fanout.status_code == 202
        assert fanout.json()["enqueued"] == 1
        assert "should-hide" not in json.dumps(fanout.json())

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        sender=ChannelNotificationSender(),
    )
    result = await worker.run_due_once(now=datetime(2026, 12, 31, 10, 0, tzinfo=UTC))
    assert result.delivered == 1

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="42")
        mine = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="99")
        other = client.get("/api/v1/in-app-messages/")

    assert mine.status_code == 200
    assert mine.json()["total"] == 1
    assert mine.json()["items"][0]["body"] == "host key changed"
    assert "should-hide" not in json.dumps(mine.json())
    assert other.json()["total"] == 0

    async with session_factory() as session:
        delivery = await session.get(NotificationDelivery, fanout.json()["delivery_ids"][0])
        stored = (await session.execute(select(InAppMessage))).scalars().all()
    assert delivery is not None
    assert delivery.status == "delivered"
    assert stored[0].body == "host key changed"


@pytest.mark.asyncio
async def test_channel_sender_posts_official_host_without_leaking_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, text="password=super-secret")

    endpoint = WebhookEndpoint(
        tenant_id="tenant-a",
        name="ops-feishu",
        url="https://open.feishu.cn/open-apis/bot/v2/hook",
        event_types_json=json.dumps(["audit.event.created"]),
        channel_type="feishu",
        credential_encrypted=encrypt_field("hook-token"),
        status="active",
    )
    delivery = NotificationDelivery(
        tenant_id="tenant-a",
        notification_rule_id=1,
        webhook_endpoint_id=1,
        event_type="audit.event.created",
        payload_json="{}",
        status="pending",
        attempts=0,
        next_attempt_at=datetime(2026, 9, 12, tzinfo=UTC),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelNotificationSender(http_client=client)
        with pytest.raises(RuntimeError) as exc_info:
            await sender.send(
                endpoint=endpoint,
                delivery=delivery,
                payload={"token": "[REDACTED]"},
            )
    assert str(exc_info.value) == "feishu delivery failed with status 503"
    assert "super-secret" not in str(exc_info.value)
    assert "hook-token" not in str(exc_info.value)
    assert str(seen[0].url).endswith("/open-apis/bot/v2/hook/hook-token")


@pytest.mark.asyncio
async def test_email_sender_uses_injected_smtp_and_redacted_body() -> None:
    class RecordingMail(SmtpMailSender):
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def send_mail(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    mail = RecordingMail()
    sender = ChannelNotificationSender(mail_sender=mail)
    endpoint = WebhookEndpoint(
        tenant_id="tenant-a",
        name="ops-mail",
        url="smtps://smtp.example.test:465",
        event_types_json="[]",
        channel_type="email",
        credential_encrypted=encrypt_field("smtp-pass"),
        recipient="ops@example.test",
        auth_username="ops@example.test",
        status="active",
    )
    await sender.send(
        endpoint=endpoint,
        delivery=NotificationDelivery(
            tenant_id="tenant-a",
            notification_rule_id=1,
            webhook_endpoint_id=1,
            event_type="session.closed",
            payload_json="{}",
            status="pending",
            attempts=0,
            next_attempt_at=datetime(2026, 9, 12, tzinfo=UTC),
        ),
        payload={"message": "done", "password": "nope"},
    )
    assert mail.calls[0]["to_addr"] == "ops@example.test"
    assert mail.calls[0]["password"] == "smtp-pass"
    assert "nope" not in str(mail.calls[0]["body"])


@pytest.mark.asyncio
async def test_cross_tenant_subscription_is_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "siem",
                "url": "https://siem.example.test/hook",
                "event_types": ["audit.event.created"],
            },
        )
        endpoint_id = created.json()["id"]
        install_user(tenant_id="tenant-b", permissions=["admin"])
        denied = client.post(
            "/api/v1/notification-subscriptions/",
            json={"event_type": "audit.event.created", "webhook_endpoint_id": endpoint_id},
        )
    assert denied.status_code == 404
    assert denied.json()["detail"] == "WEBHOOK_ENDPOINT_NOT_FOUND"
