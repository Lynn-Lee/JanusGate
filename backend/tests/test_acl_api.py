"""#t65 命令过滤 ACL 与数据脱敏规则的租户隔离 CRUD 测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db, get_read_db
from app.core.deps import current_user
from app.main import app


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


def _acl_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "deny-rm",
        "priority": 10,
        "action": "reject",
        "command_groups": [
            {"name": "danger", "match_type": "command", "patterns": ["rm", "shutdown"]}
        ],
    }
    payload.update(overrides)
    return payload


def _mask_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "ssn",
        "priority": 10,
        "match_type": "regex",
        "patterns": [r"\d{3}-\d{2}-\d{4}"],
        "mask_method": "full",
        "placeholder": "[SSN]",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_command_filter_acl_crud_is_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post("/api/v1/command-filter-acls/", json=_acl_payload())
        listed_a = client.get("/api/v1/command-filter-acls/")
        fetched_a = client.get(f"/api/v1/command-filter-acls/{created.json()['id']}")
        patched = client.patch(
            f"/api/v1/command-filter-acls/{created.json()['id']}",
            json={"priority": 5, "name": "deny-rm-updated"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        listed_b = client.get("/api/v1/command-filter-acls/")
        fetched_b = client.get(f"/api/v1/command-filter-acls/{created.json()['id']}")
        patched_b = client.patch(
            f"/api/v1/command-filter-acls/{created.json()['id']}",
            json={"priority": 99},
        )
        deleted_b = client.delete(f"/api/v1/command-filter-acls/{created.json()['id']}")

        install_user(tenant_id="tenant-a", permissions=["admin"])
        deleted_a = client.delete(f"/api/v1/command-filter-acls/{created.json()['id']}")
        missing_a = client.get(f"/api/v1/command-filter-acls/{created.json()['id']}")

    assert created.status_code == 201
    body = created.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["name"] == "deny-rm"
    assert body["action"] == "reject"
    assert body["priority"] == 10
    assert body["command_groups"][0]["patterns"] == ["rm", "shutdown"]
    assert listed_a.status_code == 200
    assert listed_a.json()["total"] == 1
    assert fetched_a.status_code == 200
    assert patched.status_code == 200
    assert patched.json()["priority"] == 5
    assert patched.json()["name"] == "deny-rm-updated"

    assert listed_b.status_code == 200
    assert listed_b.json() == {"items": [], "total": 0}
    assert fetched_b.status_code == 404
    assert fetched_b.json()["code"] == "COMMAND_FILTER_ACL_NOT_FOUND"
    assert patched_b.status_code == 404
    assert patched_b.json()["code"] == "COMMAND_FILTER_ACL_NOT_FOUND"
    assert deleted_b.status_code == 404
    assert deleted_b.json()["code"] == "COMMAND_FILTER_ACL_NOT_FOUND"

    assert deleted_a.status_code == 204
    assert missing_a.status_code == 404


@pytest.mark.asyncio
async def test_data_masking_rule_crud_is_tenant_isolated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["admin"])
        created = client.post("/api/v1/data-masking-rules/", json=_mask_payload())
        listed_a = client.get("/api/v1/data-masking-rules/")
        fetched_a = client.get(f"/api/v1/data-masking-rules/{created.json()['id']}")
        patched = client.patch(
            f"/api/v1/data-masking-rules/{created.json()['id']}",
            json={"placeholder": "[REDACTED-SSN]"},
        )

        install_user(tenant_id="tenant-b", permissions=["admin"])
        listed_b = client.get("/api/v1/data-masking-rules/")
        fetched_b = client.get(f"/api/v1/data-masking-rules/{created.json()['id']}")
        patched_b = client.patch(
            f"/api/v1/data-masking-rules/{created.json()['id']}",
            json={"placeholder": "x"},
        )
        deleted_b = client.delete(f"/api/v1/data-masking-rules/{created.json()['id']}")

        install_user(tenant_id="tenant-a", permissions=["admin"])
        deleted_a = client.delete(f"/api/v1/data-masking-rules/{created.json()['id']}")

    assert created.status_code == 201
    body = created.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["placeholder"] == "[SSN]"
    assert body["patterns"] == [r"\d{3}-\d{2}-\d{4}"]
    assert listed_a.json()["total"] == 1
    assert fetched_a.status_code == 200
    assert patched.json()["placeholder"] == "[REDACTED-SSN]"

    assert listed_b.json() == {"items": [], "total": 0}
    assert fetched_b.status_code == 404
    assert fetched_b.json()["code"] == "DATA_MASKING_RULE_NOT_FOUND"
    assert patched_b.status_code == 404
    assert deleted_b.status_code == 404
    assert deleted_a.status_code == 204


@pytest.mark.asyncio
async def test_acl_crud_requires_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    install_db(session_factory)

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=[])
        create_acl = client.post("/api/v1/command-filter-acls/", json=_acl_payload())
        list_acl = client.get("/api/v1/command-filter-acls/")
        create_mask = client.post("/api/v1/data-masking-rules/", json=_mask_payload())
        list_mask = client.get("/api/v1/data-masking-rules/")

    assert create_acl.status_code == 403
    assert list_acl.status_code == 403
    assert create_mask.status_code == 403
    assert list_mask.status_code == 403
