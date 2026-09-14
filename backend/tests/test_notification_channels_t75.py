"""Phase 6 #t75 notification channel, subscription and inbox tests."""
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
    InAppMessage,
    NotificationDelivery,
    NotificationRule,
    SystemMessageSubscription,
    WebhookEndpoint,
)
from app.services.notification_channels import (
    CHANNEL_DINGTALK,
    CHANNEL_FEISHU,
    CHANNEL_SLACK,
    CHANNEL_WECOM,
    ChannelTargetError,
    build_delivery_url,
    channel_request_body,
    redact_notification_payload,
    sanitize_channel_target,
)
from app.services.notification_delivery_worker import (
    ChannelAwareNotificationSender,
    NotificationDeliveryWorker,
)
from app.services.notification_fanout import fanout_notification_event


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


def test_sanitize_strips_im_tokens_and_rejects_unofficial_hosts() -> None:
    dingtalk = sanitize_channel_target(
        channel_type=CHANNEL_DINGTALK,
        url="https://oapi.dingtalk.com/robot/send?access_token=dt-secret",
    )
    assert dingtalk.url == "https://oapi.dingtalk.com/robot/send"
    assert dingtalk.credential == "dt-secret"
    assert "dt-secret" not in dingtalk.url

    feishu = sanitize_channel_target(
        channel_type=CHANNEL_FEISHU,
        url="https://open.feishu.cn/open-apis/bot/v2/hook/fs-secret",
    )
    assert feishu.url == "https://open.feishu.cn/open-apis/bot/v2/hook"
    assert feishu.credential == "fs-secret"

    wecom = sanitize_channel_target(
        channel_type=CHANNEL_WECOM,
        url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wc-secret",
    )
    assert wecom.url == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
    assert wecom.credential == "wc-secret"

    slack = sanitize_channel_target(
        channel_type=CHANNEL_SLACK,
        url="https://hooks.slack.com/services/T1/B2/slack-secret",
    )
    assert slack.url == "https://hooks.slack.com/services"
    assert slack.credential == "T1/B2/slack-secret"

    with pytest.raises(ChannelTargetError) as unofficial:
        sanitize_channel_target(
            channel_type=CHANNEL_DINGTALK,
            url="https://evil.example/robot/send?access_token=dt-secret",
        )
    assert unofficial.value.code == "INVALID_CHANNEL_HOST"

    with pytest.raises(ChannelTargetError) as plaintext:
        sanitize_channel_target(
            channel_type="webhook",
            url="http://siem.example.test/janusgate",
        )
    assert plaintext.value.code == "INVALID_WEBHOOK_URL"

    with pytest.raises(ChannelTargetError) as userinfo:
        sanitize_channel_target(
            channel_type="webhook",
            url="https://user:pass@siem.example.test/janusgate",
        )
    assert userinfo.value.code == "INVALID_WEBHOOK_URL"

    lark = sanitize_channel_target(
        channel_type="lark",
        url="https://open.larksuite.com/open-apis/bot/v2/hook/lark-secret",
    )
    assert lark.url == "https://open.larksuite.com/open-apis/bot/v2/hook"
    assert lark.credential == "lark-secret"

    sms = sanitize_channel_target(
        channel_type="sms",
        url="https://notify.example.test/sms",
        credential="gateway-secret",
    )
    assert sms.url == "https://notify.example.test/sms"
    assert sms.credential == "gateway-secret"

    with pytest.raises(ChannelTargetError) as missing_gateway:
        sanitize_channel_target(
            channel_type="email",
            url="https://notify.example.test/email",
        )
    assert missing_gateway.value.code == "INVALID_CHANNEL_CREDENTIAL"

    with pytest.raises(ChannelTargetError) as unknown:
        sanitize_channel_target(channel_type="pagerduty", url="https://example.test/hook")
    assert unknown.value.code == "INVALID_CHANNEL_TYPE"


def test_redact_and_im_bodies_never_echo_secrets() -> None:
    redacted = redact_notification_payload(
        {"audit_event_id": "evt-1", "token": "live-token", "note": "password=hunter2"}
    )
    assert redacted == {
        "audit_event_id": "evt-1",
        "token": "[REDACTED]",
        "note": "password=[REDACTED]",
    }
    body = channel_request_body(
        channel_type=CHANNEL_DINGTALK,
        event_type="audit.event.created",
        delivery_id=9,
        payload=redacted,
    )
    assert body["msgtype"] == "text"
    content = body["text"]["content"]
    assert "live-token" not in content
    assert "hunter2" not in content
    rebuilt = build_delivery_url(
        channel_type=CHANNEL_DINGTALK,
        stored_url="https://oapi.dingtalk.com/robot/send",
        credential="dt-secret",
    )
    assert rebuilt.endswith("access_token=dt-secret")


@pytest.mark.asyncio
async def test_channel_api_creates_im_without_echoing_credential(
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
                "url": "https://oapi.dingtalk.com/robot/send?access_token=dt-secret",
                "event_types": ["audit.event.created"],
            },
        )
        listed = client.get("/api/v1/webhook-endpoints/")
        unofficial = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "evil-bot",
                "channel_type": "dingtalk",
                "url": "https://evil.example/robot/send?access_token=dt-secret",
                "event_types": ["audit.event.created"],
            },
        )

    assert created.status_code == 201
    body = created.json()
    assert body["channel_type"] == "dingtalk"
    assert body["url"] == "https://oapi.dingtalk.com/robot/send"
    assert body["credential_configured"] is True
    assert "dt-secret" not in json.dumps(body)
    assert listed.json()["items"][0]["url"] == "https://oapi.dingtalk.com/robot/send"
    assert unofficial.status_code == 400
    assert unofficial.json()["code"] == "INVALID_CHANNEL_HOST"


@pytest.mark.asyncio
async def test_inbox_requires_subscription_and_recipient(
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
        inbox_id = inbox.json()["id"]
        rule = client.post(
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
        install_user(tenant_id="tenant-b", permissions=["admin"], user_id="user-b")
        other_tenant = client.get("/api/v1/system-message-subscriptions/")

    assert rule.status_code == 400
    assert rule.json()["code"] == "INBOX_CHANNEL_REQUIRES_SUBSCRIPTION"
    assert missing_recipient.status_code == 400
    assert missing_recipient.json()["code"] == "INBOX_RECIPIENT_REQUIRED"
    assert created.status_code == 201
    assert created.json()["recipient_user_id"] == "user-1"
    assert created.json()["channel_type"] == "inbox"
    assert other_tenant.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_fanout_redacts_payload_and_queues_rule_plus_inbox(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        webhook = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
            },
        ).json()
        inbox = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "inbox",
                "channel_type": "inbox",
                "event_types": ["audit.event.created"],
            },
        ).json()
        client.post(
            "/api/v1/notification-rules/",
            json={
                "name": "audit-to-siem",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": webhook["id"],
            },
        )
        client.post(
            "/api/v1/system-message-subscriptions/",
            json={
                "name": "audit-to-inbox",
                "event_types": ["audit.event.created"],
                "webhook_endpoint_id": inbox["id"],
                "recipient_user_id": "user-1",
            },
        )
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "audit.event.created",
                "payload": {"audit_event_id": "evt-1", "token": "live-token"},
            },
        )
        deliveries = client.get("/api/v1/notification-deliveries/").json()

    assert fanout.status_code == 202
    assert fanout.json() == {
        "event_type": "audit.event.created",
        "queued": 2,
        "inbox_queued": 1,
    }
    assert "live-token" not in json.dumps(fanout.json())
    assert deliveries["total"] == 2
    assert all("payload" not in item for item in deliveries["items"])

    async with session_factory() as session:
        stored = (await session.execute(NotificationDelivery.__table__.select())).mappings().all()
    payloads = [json.loads(row["payload_json"]) for row in stored]
    assert all(item["token"] == "[REDACTED]" for item in payloads)
    assert all("live-token" not in json.dumps(item) for item in payloads)


@pytest.mark.asyncio
async def test_channel_sender_posts_im_and_dead_letters_without_secret_leak(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 4, 5, 40, tzinfo=UTC)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, text="downstream rejected token=live-token")

    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="ops-dingtalk",
            url="https://oapi.dingtalk.com/robot/send",
            channel_type="dingtalk",
            event_types_json=json.dumps(["audit.event.created"]),
            credential_encrypted=encrypt_field("dt-secret"),
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
            payload_json=json.dumps({"audit_event_id": "evt-1", "token": "[REDACTED]"}),
            status="pending",
            attempts=2,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelAwareNotificationSender(
            session_factory=session_factory, http_client=client
        )
        worker = NotificationDeliveryWorker(
            session_factory=session_factory, sender=sender, max_attempts=3
        )
        result = await worker.run_due_once(now=now)

    assert result.dead_lettered == 1
    assert len(seen) == 1
    assert str(seen[0].url).startswith("https://oapi.dingtalk.com/robot/send")
    assert "access_token=dt-secret" in str(seen[0].url)
    body = json.loads(seen[0].content)
    assert body["msgtype"] == "text"
    assert "live-token" not in body["text"]["content"]

    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, delivery_id)
    assert stored is not None
    assert stored.status == "dead_letter"
    assert stored.last_error == "notification delivery failed with status 503"
    assert "dt-secret" not in (stored.last_error or "")
    assert "live-token" not in (stored.last_error or "")


@pytest.mark.asyncio
async def test_inbox_worker_writes_user_scoped_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 4, 5, 40, tzinfo=UTC)
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
        subscription = SystemMessageSubscription(
            tenant_id="tenant-a",
            name="audit-inbox",
            event_types_json=json.dumps(["audit.event.created"]),
            webhook_endpoint_id=endpoint.id,
            recipient_user_id="user-1",
            status="active",
        )
        session.add(subscription)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id="tenant-a",
            subscription_id=subscription.id,
            webhook_endpoint_id=endpoint.id,
            recipient_user_id="user-1",
            event_type="audit.event.created",
            payload_json=json.dumps({"audit_event_id": "evt-1", "token": "[REDACTED]"}),
            status="pending",
            attempts=0,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()

    sender = ChannelAwareNotificationSender(session_factory=session_factory)
    worker = NotificationDeliveryWorker(session_factory=session_factory, sender=sender)
    result = await worker.run_due_once(now=now)
    assert result.delivered == 1

    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[], user_id="user-1")
        own = client.get("/api/v1/in-app-messages/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="user-2")
        other = client.get("/api/v1/in-app-messages/")

    assert own.status_code == 200
    assert own.json()["total"] == 1
    message = own.json()["items"][0]
    assert message["event_type"] == "audit.event.created"
    assert message["body"]["token"] == "[REDACTED]"
    assert "live-token" not in json.dumps(message)
    assert other.json() == {"items": [], "total": 0}

    async with session_factory() as session:
        stored = (await session.execute(InAppMessage.__table__.select())).mappings().all()
    assert len(stored) == 1
    assert stored[0]["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_email_gateway_uses_bearer_and_keeps_query_off_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 4, 5, 40, tzinfo=UTC)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"accepted": True})

    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="email-gateway",
            url="https://notify.example.test/email",
            channel_type="email",
            event_types_json=json.dumps(["audit.event.created"]),
            credential_encrypted=encrypt_field("gateway-secret"),
            status="active",
        )
        session.add(endpoint)
        await session.flush()
        rule = NotificationRule(
            tenant_id="tenant-a",
            name="audit-to-email",
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
            attempts=0,
            next_attempt_at=now - timedelta(seconds=1),
        )
        session.add(delivery)
        await session.commit()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ChannelAwareNotificationSender(
            session_factory=session_factory, http_client=client
        )
        worker = NotificationDeliveryWorker(session_factory=session_factory, sender=sender)
        result = await worker.run_due_once(now=now)

    assert result.delivered == 1
    assert len(seen) == 1
    assert str(seen[0].url) == "https://notify.example.test/email"
    assert seen[0].headers["authorization"] == "Bearer gateway-secret"
    body = json.loads(seen[0].content)
    assert body["event_type"] == "audit.event.created"
    assert "gateway-secret" not in json.dumps(body)


@pytest.mark.asyncio
async def test_sms_channel_api_requires_gateway_credential(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        missing = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "sms-gateway",
                "channel_type": "sms",
                "url": "https://notify.example.test/sms",
                "event_types": ["audit.event.created"],
            },
        )
        created = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "sms-gateway",
                "channel_type": "sms",
                "url": "https://notify.example.test/sms?token=should-not-stay",
                "credential": "gateway-secret",
                "event_types": ["audit.event.created"],
            },
        )

    assert missing.status_code == 400
    assert missing.json()["code"] == "INVALID_CHANNEL_CREDENTIAL"
    assert created.status_code == 201
    body = created.json()
    assert body["channel_type"] == "sms"
    assert body["url"] == "https://notify.example.test/sms"
    assert body["credential_configured"] is True
    assert "gateway-secret" not in json.dumps(body)
    assert "should-not-stay" not in json.dumps(body)


@pytest.mark.asyncio
async def test_fanout_skips_inbox_without_recipient_and_dedupes_channel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        webhook = WebhookEndpoint(
            tenant_id="tenant-a",
            name="siem",
            url="https://siem.example.test/janusgate",
            channel_type="webhook",
            event_types_json=json.dumps(["audit.event.created"]),
            status="active",
        )
        inbox = WebhookEndpoint(
            tenant_id="tenant-a",
            name="inbox",
            url="",
            channel_type="inbox",
            event_types_json=json.dumps(["audit.event.created"]),
            status="active",
        )
        session.add_all([webhook, inbox])
        await session.flush()
        session.add_all(
            [
                NotificationRule(
                    tenant_id="tenant-a",
                    name="audit-to-siem",
                    event_types_json=json.dumps(["audit.event.created"]),
                    webhook_endpoint_id=webhook.id,
                    status="active",
                ),
                SystemMessageSubscription(
                    tenant_id="tenant-a",
                    name="audit-to-siem-again",
                    event_types_json=json.dumps(["audit.event.created"]),
                    webhook_endpoint_id=webhook.id,
                    status="active",
                ),
                SystemMessageSubscription(
                    tenant_id="tenant-a",
                    name="inbox-missing-recipient",
                    event_types_json=json.dumps(["audit.event.created"]),
                    webhook_endpoint_id=inbox.id,
                    status="active",
                ),
                SystemMessageSubscription(
                    tenant_id="tenant-a",
                    name="inbox-ok",
                    event_types_json=json.dumps(["audit.event.created"]),
                    webhook_endpoint_id=inbox.id,
                    recipient_user_id="user-1",
                    status="active",
                ),
            ]
        )
        await session.commit()
        result = await fanout_notification_event(
            db=session,
            tenant_id="tenant-a",
            event_type="audit.event.created",
            payload={"audit_event_id": "evt-1", "token": "live-token"},
        )

    assert result.queued == 2
    assert result.inbox_queued == 1
    async with session_factory() as session:
        stored = (await session.execute(NotificationDelivery.__table__.select())).mappings().all()
    assert len(stored) == 2
    payloads = [json.loads(row["payload_json"]) for row in stored]
    assert all(item["token"] == "[REDACTED]" for item in payloads)
    assert {row["recipient_user_id"] for row in stored} == {None, "user-1"}
