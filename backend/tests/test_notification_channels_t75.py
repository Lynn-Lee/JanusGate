"""#t75 通知渠道扩展：IM sanitization、订阅扇出、站内信隔离与 dead-letter 契约。"""
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
from app.core.security import encrypt_field
from app.main import app
from app.models.webhook import (
    NotificationDelivery,
    NotificationRule,
    WebhookEndpoint,
)
from app.services.notification_channels import (
    CHANNEL_DINGTALK,
    CHANNEL_EMAIL,
    CHANNEL_INBOX,
    ChannelTargetError,
    build_delivery_url,
    sanitize_channel_target,
)
from app.services.notification_delivery_worker import (
    ChannelAwareNotificationSender,
    NotificationDeliveryWorker,
)


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


def test_sanitize_dingtalk_strips_access_token_and_requires_official_host() -> None:
    target = sanitize_channel_target(
        channel_type="dingtalk",
        url="https://oapi.dingtalk.com/robot/send?access_token=ding-secret",
    )
    assert target.channel_type == CHANNEL_DINGTALK
    assert target.url == "https://oapi.dingtalk.com/robot/send"
    assert target.credential == "ding-secret"
    assert "ding-secret" not in target.url
    rebuilt = build_delivery_url(
        channel_type=CHANNEL_DINGTALK, stored_url=target.url, credential=target.credential
    )
    assert rebuilt.endswith("access_token=ding-secret")

    with pytest.raises(ChannelTargetError, match="INVALID_CHANNEL_HOST"):
        sanitize_channel_target(
            channel_type="dingtalk",
            url="https://evil.example.test/robot/send?access_token=ding-secret",
        )


def test_sanitize_feishu_and_slack_strip_path_secrets() -> None:
    feishu = sanitize_channel_target(
        channel_type="feishu",
        url="https://open.feishu.cn/open-apis/bot/v2/hook/hook-secret",
    )
    assert feishu.url == "https://open.feishu.cn/open-apis/bot/v2/hook"
    assert feishu.credential == "hook-secret"
    slack = sanitize_channel_target(
        channel_type="slack",
        url="https://hooks.slack.com/services/T1/B2/slack-secret",
    )
    assert slack.url == "https://hooks.slack.com/services"
    assert slack.credential == "T1/B2/slack-secret"


def test_sanitize_webhook_keeps_query_but_rejects_userinfo() -> None:
    target = sanitize_channel_target(
        channel_type="webhook",
        url="https://siem.example.test/janusgate?source=pam",
    )
    assert target.url == "https://siem.example.test/janusgate?source=pam"
    assert target.credential is None
    with pytest.raises(ChannelTargetError, match="INVALID_WEBHOOK_URL"):
        sanitize_channel_target(
            channel_type="webhook",
            url="https://user:pass@siem.example.test/janusgate",
        )


@pytest.mark.asyncio
async def test_webhook_api_creates_im_channel_without_leaking_credential(
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
                "url": "https://oapi.dingtalk.com/robot/send?access_token=ding-secret",
                "event_types": ["workflow.request.approved"],
            },
        )
        listed = client.get("/api/v1/webhook-endpoints/")

    assert created.status_code == 201
    body = created.json()
    assert body["channel_type"] == "dingtalk"
    assert body["url"] == "https://oapi.dingtalk.com/robot/send"
    assert body["credential_configured"] is True
    assert "ding-secret" not in created.text
    assert "credential" not in body
    assert listed.json()["items"][0]["url"] == body["url"]

    async with session_factory() as session:
        endpoint = await session.get(WebhookEndpoint, body["id"])
    assert endpoint is not None
    assert endpoint.credential_encrypted
    assert "ding-secret" not in endpoint.credential_encrypted
    assert "ding-secret" not in endpoint.url


@pytest.mark.asyncio
async def test_inbox_channel_and_subscription_require_recipient(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        inbox = client.post(
            "/api/v1/webhook-endpoints/",
            json={"name": "inbox", "channel_type": "inbox", "event_types": ["audit.event.created"]},
        )
        assert inbox.status_code == 201
        inbox_id = inbox.json()["id"]
        rejected_rule = client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "inbox-rule",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox_id,
            },
        )
        missing_recipient = client.post(
            "/api/v1/system-message-subscriptions/",
            json={
                "name": "inbox-sub",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox_id,
            },
        )
        created = client.post(
            "/api/v1/system-message-subscriptions/",
            json={
                "name": "inbox-sub",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox_id,
                "recipient_user_id": "user-1",
            },
        )
    assert rejected_rule.status_code == 400
    assert rejected_rule.json()["code"] == "INBOX_CHANNEL_REQUIRES_SUBSCRIPTION"
    assert missing_recipient.status_code == 400
    assert missing_recipient.json()["code"] == "INBOX_RECIPIENT_REQUIRED"
    assert created.status_code == 201
    assert created.json()["recipient_user_id"] == "user-1"
    assert created.json()["channel_type"] == "inbox"


@pytest.mark.asyncio
async def test_notification_event_fanout_writes_inbox_for_current_user_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="user-1")
        inbox = client.post(
            "/api/v1/webhook-endpoints/",
            json={"name": "inbox", "channel_type": "inbox", "event_types": ["audit.event.created"]},
        ).json()
        client.post(
            "/api/v1/system-message-subscriptions/",
            json={
                "name": "alice-inbox",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox["id"],
                "recipient_user_id": "user-1",
            },
        )
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "audit.event.created",
                "payload": {"audit_event_id": "evt-1", "token": "super-secret"},
            },
        )
        assert fanout.status_code == 202
        assert fanout.json() == {
            "event_type": "audit.event.created",
            "queued": 1,
            "inbox_queued": 1,
        }
        assert "super-secret" not in fanout.text

        worker = NotificationDeliveryWorker(
            session_factory=session_factory,
            sender=ChannelAwareNotificationSender(session_factory=session_factory),
        )
        result = await worker.run_due_once(now=datetime(2026, 9, 14, 6, 0, tzinfo=UTC))
        assert result.delivered == 1

        own_inbox = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="user-2")
        other_inbox = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-b", permissions=["admin"], user_id="user-1")
        cross_tenant = client.get("/api/v1/in-app-messages/")

    assert own_inbox.status_code == 200
    items = own_inbox.json()["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "audit.event.created"
    assert items[0]["body"]["token"] == "[REDACTED]"
    assert "super-secret" not in own_inbox.text
    assert other_inbox.json() == {"items": [], "total": 0}
    assert cross_tenant.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_email_gateway_sender_uses_bearer_and_hides_downstream_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, text="smtp password=super-secret rejected")

    endpoint = WebhookEndpoint(
        tenant_id="tenant-a",
        name="email-gw",
        url="https://notify.example.test/email",
        channel_type=CHANNEL_EMAIL,
        event_types_json=json.dumps(["audit.event.created"]),
        credential_encrypted=encrypt_field("email-bearer"),
        status="active",
    )
    delivery = NotificationDelivery(
        tenant_id="tenant-a",
        notification_rule_id=1,
        webhook_endpoint_id=1,
        event_type="audit.event.created",
        payload_json=json.dumps({"password": "[REDACTED]"}),
        status="pending",
        attempts=0,
        next_attempt_at=datetime(2026, 9, 14, 6, 0, tzinfo=UTC),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelAwareNotificationSender(
            session_factory=async_sessionmaker(create_async_engine("sqlite+aiosqlite:///:memory:")),
            http_client=client,
        )
        with pytest.raises(RuntimeError) as exc_info:
            await sender.send(
                endpoint=endpoint,
                delivery=delivery,
                payload={"password": "[REDACTED]"},
            )

    assert seen[0].headers["authorization"] == "Bearer email-bearer"
    message = str(exc_info.value)
    assert message == "notification delivery failed with status 503"
    assert "super-secret" not in message
    assert "email-bearer" not in message
    assert "password" not in message


@pytest.mark.asyncio
async def test_im_sender_dead_letters_without_leaking_bot_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="ops-dingtalk",
            url="https://oapi.dingtalk.com/robot/send",
            channel_type=CHANNEL_DINGTALK,
            event_types_json=json.dumps(["audit.event.created"]),
            credential_encrypted=encrypt_field("ding-secret"),
            status="active",
        )
        session.add(endpoint)
        await session.flush()
        rule = NotificationRule(
            tenant_id="tenant-a",
            name="audit-to-dingtalk",
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
            payload_json=json.dumps({"audit_event_id": "evt-1"}),
            status="pending",
            attempts=2,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    def handler(request: httpx.Request) -> httpx.Response:
        assert "access_token=ding-secret" in str(request.url)
        return httpx.Response(500, text="bot token ding-secret rejected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelAwareNotificationSender(
            session_factory=session_factory, http_client=client
        )
        worker = NotificationDeliveryWorker(
            session_factory=session_factory, sender=sender, max_attempts=3
        )
        result = await worker.run_due_once(now=now)

    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, delivery_id)
    assert result.dead_lettered == 1
    assert stored is not None
    assert stored.status == "dead_letter"
    assert stored.last_error == "notification delivery failed with status 500"
    assert stored.last_error is not None
    assert "ding-secret" not in stored.last_error


@pytest.mark.asyncio
async def test_cross_tenant_subscriptions_are_isolated(
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
        ).json()
        client.post(
            "/api/v1/system-message-subscriptions/",
            json={
                "name": "tenant-a-sub",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": endpoint["id"],
            },
        )
        tenant_a = client.get("/api/v1/system-message-subscriptions/")
        install_user(tenant_id="tenant-b", permissions=["admin"])
        tenant_b = client.get("/api/v1/system-message-subscriptions/")
        stolen = client.post(
            "/api/v1/system-message-subscriptions/",
            json={
                "name": "stolen",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": endpoint["id"],
            },
        )
    assert tenant_a.json()["total"] == 1
    assert tenant_b.json() == {"items": [], "total": 0}
    assert stolen.status_code == 404
    assert stolen.json()["code"] == "WEBHOOK_ENDPOINT_NOT_FOUND"
