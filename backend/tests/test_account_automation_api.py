"""#t73 账号模板 / 风险 / 自动化调度 API 契约测试。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user, get_redis
from app.main import app
from app.models.account import Account, AccountRisk, AccountTemplate
from app.models.asset import Asset, Platform


class RecordingRedisStream:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], int | None]] = []

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self.calls.append((name, fields, maxlen))
        return "1700000000000-0"


def install_user(*, tenant_id: str, permissions: list[str]) -> None:
    app.dependency_overrides[current_user] = lambda: {
        "id": "user-1",
        "username": "alice",
        "tenant_id": tenant_id,
        "organization_id": None,
        "team_id": None,
        "project_id": None,
        "permissions": permissions,
    }


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


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Platform(id=1, name="Linux", category="host", protocols='["ssh"]'))
        session.add(
            Asset(
                id=1,
                name="prod-linux",
                address="203.0.113.10",
                platform_id=1,
                tenant_id="tenant-a",
            )
        )
        session.add(
            Account(
                tenant_id="tenant-a",
                asset_id=1,
                username="deploy",
                protocol="ssh",
                secret_id="sec_tenant_a_deploy",
            )
        )
        session.add(
            AccountRisk(
                tenant_id="tenant-a",
                asset_id=1,
                username="root",
                risk_type="privileged",
                severity="high",
                detail="privileged account discovered on host",
                status="open",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_account_template_crud_is_tenant_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed(session_factory)
    install_user(tenant_id="tenant-a", permissions=["accounts:write", "accounts:read"])

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/account-templates/",
            json={"name": "ops", "username": "ops", "groups": ["sudo"]},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["username"] == "ops"
        assert body["groups"] == ["sudo"]
        assert "password" not in body

        listed = client.get("/api/v1/account-templates/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        install_user(tenant_id="tenant-b", permissions=["accounts:read"])
        other = client.get("/api/v1/account-templates/")
        assert other.json()["total"] == 0


@pytest.mark.asyncio
async def test_account_risk_list_and_resolve(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed(session_factory)
    install_user(tenant_id="tenant-a", permissions=["accounts:read", "accounts:automate"])

    with TestClient(app) as client:
        listed = client.get("/api/v1/account-risks/")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        risk_id = listed.json()["items"][0]["id"]
        resolved = client.post(
            f"/api/v1/account-risks/{risk_id}/resolve",
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_enqueue_account_jobs_json_only_and_scoped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed(session_factory)
    redis = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: redis
    install_user(
        tenant_id="tenant-a",
        permissions=["accounts:automate", "accounts:write", "accounts:read"],
    )

    async with session_factory() as session:
        session.add(
            AccountTemplate(tenant_id="tenant-a", name="ops-user", username="ops", protocol="ssh")
        )
        await session.commit()

    endpoints = [
        ("/api/v1/automation/jobs/account-change-secret", {"account_id": 1}, "account.change_secret"),
        ("/api/v1/automation/jobs/account-verify", {"account_id": 1}, "account.verify"),
        ("/api/v1/automation/jobs/account-remove", {"account_id": 1}, "account.remove"),
        ("/api/v1/automation/jobs/account-gather", {"account_id": 1}, "account.gather"),
        ("/api/v1/automation/jobs/account-verify-gateway", {"account_id": 1}, "account.verify_gateway"),
        ("/api/v1/automation/jobs/account-check", {"account_id": 1}, "account.check"),
        ("/api/v1/automation/jobs/account-backup", {"account_id": 1}, "account.backup"),
        (
            "/api/v1/automation/jobs/account-push",
            {"asset_id": 1, "template_id": 1},
            "account.push",
        ),
    ]

    with TestClient(app) as client:
        for path, payload, job_type in endpoints:
            response = client.post(path, json=payload)
            assert response.status_code == 202, (path, response.text)
            assert response.json()["job_type"] == job_type
            assert response.json()["status"] == "queued"

        forbidden = client.post(
            "/api/v1/automation/jobs/account-change-secret",
            json={"account_id": 1, "password": "plain"},
        )
        assert forbidden.status_code == 422

        install_user(tenant_id="tenant-b", permissions=["accounts:automate"])
        missing = client.post(
            "/api/v1/automation/jobs/account-verify",
            json={"account_id": 1},
        )
        assert missing.status_code == 404

    for _name, fields, _maxlen in redis.calls:
        payload = json.loads(fields["payload_json"])
        blob = json.dumps(payload).lower()
        assert "password" not in blob
        assert "secret" not in blob
        assert fields["payload_format"] == "json"


@pytest.mark.asyncio
async def test_account_automation_requires_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)
    await seed(session_factory)
    app.dependency_overrides[get_redis] = lambda: RecordingRedisStream()
    install_user(tenant_id="tenant-a", permissions=["accounts:read"])

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/automation/jobs/account-verify",
            json={"account_id": 1},
        )
        assert response.status_code == 403
