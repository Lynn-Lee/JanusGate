"""Asset API and service regression coverage, including SSRF guardrails."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import assets as assets_api
from app.core.database import get_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Asset, Platform
from app.services import asset as asset_service_module
from app.services.asset import AssetService, _is_private_ip


class ScalarResult:
    def __init__(self, value: Any = None, values: list[Any] | None = None) -> None:
        self.value = value
        self.values = values if values is not None else ([] if value is None else [value])

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self.values


class FakeDB:
    def __init__(self, *results: ScalarResult) -> None:
        self.results = list(results)
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0

    async def execute(self, _statement: Any) -> ScalarResult:
        if not self.results:
            raise AssertionError("unexpected execute call")
        return self.results.pop(0)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = 1
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)
        if hasattr(obj, "is_active") and getattr(obj, "is_active", None) is None:
            obj.is_active = True

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def install_auth_and_db(fake_db: FakeDB | None = None) -> FakeDB:
    db = fake_db or FakeDB()
    app.dependency_overrides[current_user] = lambda: {"id": 1, "username": "alice"}
    app.dependency_overrides[get_db] = lambda: db
    return db


def async_value(value: Any) -> Any:
    async def inner(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return inner


def async_call(func: Any) -> Any:
    async def inner(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    return inner


def asset(**overrides: Any) -> Asset:
    base = {
        "id": 1,
        "name": "prod-linux",
        "address": "203.0.113.10",
        "platform_id": 1,
        "port": 22,
        "username": "root",
        "is_active": True,
        "description": "primary host",
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return Asset(**base)


def platform(**overrides: Any) -> Platform:
    base = {"id": 1, "name": "Linux", "category": "host", "protocols": '["ssh"]', "is_active": True}
    base.update(overrides)
    return Platform(**base)


def test_asset_crud_api_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    install_auth_and_db()
    monkeypatch.setattr(AssetService, "list_assets", async_call(lambda _db, skip, limit: [asset(id=skip + 1)]))
    monkeypatch.setattr(AssetService, "get_asset", async_call(lambda _db, asset_id: asset(id=asset_id) if asset_id == 1 else None))
    monkeypatch.setattr(AssetService, "create_asset", async_call(lambda _db, data: asset(id=2, **data)))
    monkeypatch.setattr(AssetService, "delete_asset", async_call(lambda _db, asset_id: asset_id == 1))

    with TestClient(app) as client:
        list_response = client.get("/api/v1/assets/", params={"skip": 0, "limit": 10})
        get_response = client.get("/api/v1/assets/1")
        missing_response = client.get("/api/v1/assets/404")
        create_response = client.post(
            "/api/v1/assets/",
            json={"name": "staging", "address": "203.0.113.11", "platform_id": 1, "port": 2222, "username": "deploy"},
        )
        delete_response = client.delete("/api/v1/assets/1")
        delete_missing_response = client.delete("/api/v1/assets/404")

    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "prod-linux"
    assert get_response.status_code == 200
    assert get_response.json()["id"] == 1
    assert missing_response.status_code == 404
    assert create_response.status_code == 200
    assert create_response.json()["port"] == 2222
    assert delete_response.json() == {"status": "ok"}
    assert delete_missing_response.status_code == 404


def test_asset_test_connection_api_blocks_private_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    install_auth_and_db()
    monkeypatch.setattr(
        AssetService,
        "test_connection",
        async_call(lambda address, port: {"reachable": False, "error": f"blocked {address}:{port}"}),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assets/test-connection",
            params={"address": "127.0.0.1", "port": 22},
        )

    assert response.status_code == 200
    assert response.json() == {"reachable": False, "error": "blocked 127.0.0.1:22"}


def test_platform_api_create_and_list_contract() -> None:
    db = install_auth_and_db(FakeDB(ScalarResult(values=[platform()])))

    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/assets/platforms",
            json={"name": "Linux", "category": "host", "protocols": '["ssh"]'},
        )

    assert create_response.status_code == 200
    assert create_response.json()["name"] == "Linux"
    assert isinstance(db.added[0], Platform)


@pytest.mark.asyncio
async def test_list_platforms_route_handler_returns_platforms_directly() -> None:
    response = await assets_api.list_platforms(FakeDB(ScalarResult(values=[platform()])), {"id": 1})

    assert response[0].name == "Linux"
    assert response[0].protocols == '["ssh"]'


@pytest.mark.parametrize("address", ["10.0.0.1", "172.16.1.10", "192.168.1.5", "127.0.0.1", "169.254.169.254", "0.1.2.3"])
def test_private_and_link_local_addresses_are_blocked(address: str) -> None:
    assert _is_private_ip(address) is True


def test_public_ip_and_hostname_are_not_private_ip_literals() -> None:
    assert _is_private_ip("8.8.8.8") is False
    assert _is_private_ip("example.com") is False


@pytest.mark.asyncio
async def test_asset_service_crud_paths() -> None:
    existing = asset()

    assert await AssetService.list_assets(FakeDB(ScalarResult(values=[existing]))) == [existing]
    assert await AssetService.get_asset(FakeDB(ScalarResult(existing)), 1) is existing

    create_db = FakeDB()
    created = await AssetService.create_asset(
        create_db,
        {"name": "created", "address": "203.0.113.12", "platform_id": 1, "port": 22},
    )
    assert created in create_db.added
    assert create_db.commits == 1

    assert await AssetService.update_asset(FakeDB(ScalarResult(None)), 404, {"name": "missing"}) is None
    updated = await AssetService.update_asset(
        FakeDB(ScalarResult(existing)),
        1,
        {"name": "renamed", "description": None, "unknown": "ignored"},
    )
    assert updated is existing
    assert existing.name == "renamed"
    assert existing.description == "primary host"

    assert await AssetService.delete_asset(FakeDB(ScalarResult(None)), 404) is False
    delete_db = FakeDB(ScalarResult(existing))
    assert await AssetService.delete_asset(delete_db, 1) is True
    assert delete_db.deleted == [existing]


@pytest.mark.asyncio
async def test_asset_service_test_connection_blocks_internal_ips_and_handles_socket_results(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = await AssetService.test_connection("169.254.169.254", 80)
    assert blocked == {"reachable": False, "error": "SSRF protection: private/internal IP blocked"}

    class FakeSocket:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_socket = FakeSocket()
    monkeypatch.setattr(asset_service_module.socket, "create_connection", lambda target, timeout: fake_socket)
    success = await AssetService.test_connection("8.8.8.8", 443, timeout=0.1)
    assert success == {"reachable": True, "error": ""}
    assert fake_socket.closed is True

    def raise_timeout(_target: tuple[str, int], timeout: float) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr(asset_service_module.socket, "create_connection", raise_timeout)
    failure = await AssetService.test_connection("8.8.8.8", 443, timeout=0.1)
    assert failure["reachable"] is False
    assert "timed out" in failure["error"]
