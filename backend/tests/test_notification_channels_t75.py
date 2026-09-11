"""#t75 通知渠道：官方 host 白名单、订阅扇出、站内信与脱敏/死信契约。"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.core.security import encrypt_field
from app.main import app
from app.models.webhook import InAppMessage, NotificationDelivery, NotificationRule, WebhookEndpoint
from app.services.notification_delivery_worker import NotificationDeliveryWorker
from app.services.notification_payload import redact_payload
from app.services.notification_senders import ChannelDispatchSender


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


def test_redact_payload_masks_token_keys_and_assignments() -> None:
    redacted = redact_payload(
        {"audit_event_id": "evt-1", "token": "raw-secret", "note": "password=plain"}
    )
    assert redacted == {
        "audit_event_id": "evt-1",
        "token": "[REDACTED]",
        "note": "password=[REDACTED]",
    }


@pytest.mark.asyncio
async def test_webhook_create_response_includes_channel_type_without_secrets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        response = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "security-siem",
                "url": "https://siem.example.test/janusgate",
                "event_types": ["audit.event.created"],
                "signing_secret": "super-secret-webhook-key",
            },
        )
    assert response.status_code == 201
    created = response.json()
    assert created["channel_type"] == "webhook"
    assert created["credential_configured"] is False
    assert created["signing_secret_configured"] is True
    assert "credential" not in created
    assert "signing_secret" not in created


@pytest.mark.asyncio
async def test_im_channel_rejects_unofficial_host_and_query_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        unofficial = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "fake-dingtalk",
                "channel_type": "dingtalk",
                "url": "https://evil.example.test/robot/send",
                "event_types": ["audit.event.created"],
                "credential": "robot-secret",
            },
        )
        leaked_query = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "dingtalk-query",
                "channel_type": "dingtalk",
                "url": "https://oapi.dingtalk.com/robot/send?access_token=abc",
                "event_types": ["audit.event.created"],
            },
        )
        official = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "dingtalk-ok",
                "channel_type": "dingtalk",
                "url": "https://oapi.dingtalk.com/robot/send",
                "event_types": ["audit.event.created"],
                "credential": "robot-secret",
            },
        )
    assert unofficial.status_code == 400
    assert unofficial.json()["code"] == "CHANNEL_HOST_NOT_ALLOWED"
    assert leaked_query.status_code == 400
    assert leaked_query.json()["code"] == "CHANNEL_CREDENTIAL_IN_URL"
    assert official.status_code == 201
    assert official.json()["url"] == "https://oapi.dingtalk.com/robot/send"
    assert official.json()["channel_type"] == "dingtalk"
    assert official.json()["credential_configured"] is True
    assert "robot-secret" not in official.text


@pytest.mark.asyncio
async def test_email_channel_requires_tls_and_rejects_credentials_in_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        plaintext = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "smtp-25",
                "channel_type": "email",
                "url": "smtp://mail.example.test",
                "event_types": ["audit.event.created"],
                "config": {"from_address": "a@x.test", "to_address": "b@x.test"},
            },
        )
        userinfo = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "smtp-userinfo",
                "channel_type": "email",
                "url": "smtps://user:pass@mail.example.test:465",
                "event_types": ["audit.event.created"],
            },
        )
        ok = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "smtp-ok",
                "channel_type": "email",
                "url": "smtps://mail.example.test:465",
                "event_types": ["audit.event.created"],
                "credential": "smtp-password",
                "config": {
                    "from_address": "noreply@janusgate.test",
                    "to_address": "ops@janusgate.test",
                    "smtp_username": "noreply",
                },
            },
        )
    assert plaintext.status_code == 400
    assert plaintext.json()["code"] == "EMAIL_STARTTLS_REQUIRED"
    assert userinfo.status_code == 400
    assert userinfo.json()["code"] == "CHANNEL_CREDENTIAL_IN_URL"
    assert ok.status_code == 201
    assert "smtp-password" not in ok.text


@pytest.mark.asyncio
async def test_subscription_fanout_redacts_payload_and_is_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="42")
        channel = client.post(
            "/api/v1/webhook-endpoints/",
            json={
                "name": "inbox",
                "channel_type": "inbox",
                "url": "inbox://local",
                "event_types": ["workflow.request.approved"],
            },
        )
        assert channel.status_code == 201, channel.text
        channel_id = channel.json()["id"]
        sub = client.post(
            "/api/v1/notification-subscriptions/",
            json={
                "name": "approvals-to-inbox",
                "user_id": "42",
                "event_types": ["workflow.request.approved"],
                "webhook_endpoint_id": channel_id,
            },
        )
        assert sub.status_code == 201, sub.text
        fanout = client.post(
            "/api/v1/notification-events/",
            json={
                "event_type": "workflow.request.approved",
                "payload": {"title": "已审批", "token": "raw-secret", "message": "password=plain"},
            },
        )
        deliveries = client.get("/api/v1/notification-deliveries/")
        install_user(tenant_id="tenant-b", permissions=["admin"])
        other = client.get("/api/v1/notification-subscriptions/")

    assert fanout.status_code == 202
    assert fanout.json()["enqueued"] == 1
    assert "raw-secret" not in fanout.text
    listed = deliveries.json()
    assert listed["total"] == 1
    assert listed["items"][0]["subscription_id"] == sub.json()["id"]
    assert listed["items"][0]["recipient_user_id"] == "42"
    assert "payload" not in listed["items"][0]
    assert other.json() == {"items": [], "total": 0}

    async with session_factory() as session:
        delivery = (await session.execute(select(NotificationDelivery))).scalar_one()
        stored = json.loads(delivery.payload_json)
        assert stored["token"] == "[REDACTED]"
        assert stored["message"] == "password=[REDACTED]"
        assert stored["title"] == "已审批"


@pytest.mark.asyncio
async def test_channel_dispatch_sender_dingtalk_and_dead_letter_hides_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 11, 2, 0, tzinfo=UTC)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, text="downstream secret boom")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    sender = ChannelDispatchSender(http_client=client)
    async with session_factory() as session:
        endpoint = WebhookEndpoint(
            tenant_id="tenant-a",
            name="dingtalk",
            url="https://oapi.dingtalk.com/robot/send",
            event_types_json=json.dumps(["audit.event.created"]),
            channel_type="dingtalk",
            credential_encrypted=encrypt_field("robot-secret"),
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
            payload_json=json.dumps({"token": "[REDACTED]", "message": "主机密钥变了"}),
            status="pending",
            attempts=2,
            next_attempt_at=now,
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        sender=sender,
        max_attempts=3,
        retry_delay=timedelta(minutes=5),
    )
    result = await worker.run_due_once(now=now)
    assert result.dead_lettered == 1
    assert requests and requests[0].url.host == "oapi.dingtalk.com"
    body = json.loads(requests[0].content.decode())
    assert body == {"msgtype": "text", "text": {"content": "主机密钥变了"}}
    async with session_factory() as session:
        stored = await session.get(NotificationDelivery, delivery_id)
        assert stored is not None
        assert stored.status == "dead_letter"
        assert stored.last_error == "notification delivery failed with status 500"
        assert "robot-secret" not in (stored.last_error or "")
        assert "downstream secret boom" not in (stored.last_error or "")


@pytest.mark.asyncio
async def test_inbox_and_email_senders_deliver_redacted_content(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 11, 2, 10, tzinfo=UTC)
    sent_mail: list[EmailMessage] = []

    class RecordingSmtp:
        def send(
            self,
            *,
            host: str,
            port: int,
            use_ssl: bool,
            username: str,
            password: str,
            message: EmailMessage,
        ) -> None:
            assert host == "mail.example.test"
            assert port == 465
            assert use_ssl is True
            assert password == "smtp-password"
            sent_mail.append(message)

    sender = ChannelDispatchSender(smtp_transport=RecordingSmtp())
    async with session_factory() as session:
        inbox = WebhookEndpoint(
            tenant_id="tenant-a",
            name="inbox",
            url="inbox://local",
            event_types_json=json.dumps(["session.closed"]),
            channel_type="inbox",
            status="active",
        )
        mail = WebhookEndpoint(
            tenant_id="tenant-a",
            name="mail",
            url="smtps://mail.example.test:465",
            event_types_json=json.dumps(["session.closed"]),
            channel_type="email",
            credential_encrypted=encrypt_field("smtp-password"),
            config_json=json.dumps(
                {
                    "from_address": "noreply@janusgate.test",
                    "to_address": "ops@janusgate.test",
                    "smtp_username": "noreply",
                }
            ),
            status="active",
        )
        session.add_all([inbox, mail])
        await session.flush()
        inbox_rule = NotificationRule(
            tenant_id="tenant-a",
            name="inbox-rule",
            event_types_json=json.dumps(["session.closed"]),
            webhook_endpoint_id=inbox.id,
            status="active",
        )
        mail_rule = NotificationRule(
            tenant_id="tenant-a",
            name="mail-rule",
            event_types_json=json.dumps(["session.closed"]),
            webhook_endpoint_id=mail.id,
            status="active",
        )
        session.add_all([inbox_rule, mail_rule])
        await session.flush()
        session.add_all(
            [
                NotificationDelivery(
                    tenant_id="tenant-a",
                    notification_rule_id=inbox_rule.id,
                    webhook_endpoint_id=inbox.id,
                    recipient_user_id="42",
                    event_type="session.closed",
                    payload_json=json.dumps({"title": "会话已关闭", "message": "token=[REDACTED]"}),
                    status="pending",
                    attempts=0,
                    next_attempt_at=now,
                ),
                NotificationDelivery(
                    tenant_id="tenant-a",
                    notification_rule_id=mail_rule.id,
                    webhook_endpoint_id=mail.id,
                    event_type="session.closed",
                    payload_json=json.dumps({"title": "会话已关闭", "message": "token=[REDACTED]"}),
                    status="pending",
                    attempts=0,
                    next_attempt_at=now,
                ),
            ]
        )
        await session.commit()

    result = await NotificationDeliveryWorker(
        session_factory=session_factory, sender=sender
    ).run_due_once(now=now)
    assert result.delivered == 2
    assert len(sent_mail) == 1
    assert sent_mail[0]["Subject"] == "会话已关闭"
    assert "token=[REDACTED]" in sent_mail[0].get_content()
    assert "smtp-password" not in sent_mail[0].as_string()

    install_db(session_factory)
    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="42")
        inbox_list = client.get("/api/v1/inbox-messages/")
        install_user(tenant_id="tenant-a", permissions=["admin"], user_id="99")
        other_user = client.get("/api/v1/inbox-messages/")
    assert inbox_list.status_code == 200
    assert inbox_list.json()["total"] == 1
    assert inbox_list.json()["items"][0]["title"] == "会话已关闭"
    assert inbox_list.json()["items"][0]["body"] == "token=[REDACTED]"
    assert other_user.json() == {"items": [], "total": 0}

    async with session_factory() as session:
        stored = (await session.execute(select(InAppMessage))).scalar_one()
        assert stored.user_id == "42"
        assert stored.body == "token=[REDACTED]"
