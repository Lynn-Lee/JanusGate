"""Database engine routing regression tests."""
from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from app.api.accounts import router as accounts_router
from app.api.assets import router as assets_router
from app.api.notification_deliveries import router as notification_deliveries_router
from app.api.notification_rules import router as notification_rules_router
from app.api.session_recordings import router as session_recordings_router
from app.api.webhook_endpoints import router as webhook_endpoints_router
from app.core.config import Settings
from app.core.database import create_database_engines, get_db, get_read_db


def test_create_database_engines_defaults_read_engine_to_writer() -> None:
    created_urls: list[str] = []

    def fake_create_engine(url: str, **_: Any) -> str:
        created_urls.append(url)
        return f"engine:{url}"

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        DATABASE_URL="postgresql+asyncpg://writer/janusgate",
        DATABASE_READ_REPLICA_URL="",
        _env_file=None,
    )

    engines = create_database_engines(settings=settings, engine_factory=fake_create_engine)

    assert engines.writer == "engine:postgresql+asyncpg://writer/janusgate"
    assert engines.reader == engines.writer
    assert created_urls == ["postgresql+asyncpg://writer/janusgate"]


def test_create_database_engines_uses_configured_read_replica() -> None:
    created_urls: list[str] = []

    def fake_create_engine(url: str, **_: Any) -> str:
        created_urls.append(url)
        return f"engine:{url}"

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        DATABASE_URL="postgresql+asyncpg://writer/janusgate",
        DATABASE_READ_REPLICA_URL="postgresql+asyncpg://reader/janusgate",
        _env_file=None,
    )

    engines = create_database_engines(settings=settings, engine_factory=fake_create_engine)

    assert engines.writer == "engine:postgresql+asyncpg://writer/janusgate"
    assert engines.reader == "engine:postgresql+asyncpg://reader/janusgate"
    assert created_urls == [
        "postgresql+asyncpg://writer/janusgate",
        "postgresql+asyncpg://reader/janusgate",
    ]


def test_asset_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/assets/"),
        ("GET", "/assets/platforms"),
        ("GET", "/assets/{asset_id}"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_asset_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/assets/"),
        ("DELETE", "/assets/{asset_id}"),
        ("POST", "/assets/platforms"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_account_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/accounts/"),
        ("GET", "/accounts/{account_id}/rotations"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(router=accounts_router, method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_account_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/accounts/"),
        ("POST", "/accounts/{account_id}/rotations"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(router=accounts_router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_session_recording_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/session-recordings/{recording_id}/commands"),
        ("GET", "/session-recordings/commands"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(
            router=session_recordings_router, method=method, path=path
        )
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_session_recording_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/sessions/{session_id}/recordings"),
        ("POST", "/session-recordings/{recording_id}/commands"),
        ("POST", "/connectors/{connector_id}/session-recordings/{recording_id}/commands"),
        ("POST", "/session-recordings/{recording_id}/close"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(
            router=session_recordings_router, method=method, path=path
        )
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_notification_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        (notification_rules_router, "GET", "/notification-rules/"),
        (notification_deliveries_router, "GET", "/notification-deliveries/"),
    ]

    for router, method, path in read_routes:
        dependencies = _route_dependency_calls(router=router, method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_notification_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        (notification_rules_router, "POST", "/notification-rules/"),
        (notification_deliveries_router, "POST", "/notification-rules/{rule_id}/deliveries"),
    ]

    for router, method, path in write_routes:
        dependencies = _route_dependency_calls(router=router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_webhook_endpoint_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/webhook-endpoints/"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(
            router=webhook_endpoints_router, method=method, path=path
        )
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_webhook_endpoint_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/webhook-endpoints/"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(
            router=webhook_endpoints_router, method=method, path=path
        )
        assert get_db in dependencies
        assert get_read_db not in dependencies


def _route_dependency_calls(*, router: Any = assets_router, method: str, path: str) -> set[Any]:
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == path and method in route.methods:
            return {dependency.call for dependency in route.dependant.dependencies}
    raise AssertionError(f"Route not found: {method} {path}")
