"""Database engine routing regression tests."""
from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

from app.api.accounts import router as accounts_router
from app.api.assets import router as assets_router
from app.api.audits.routes import router as audits_router
from app.api.auth import router as auth_router
from app.api.automation import router as automation_router
from app.api.connectors import router as connectors_router
from app.api.notification_deliveries import router as notification_deliveries_router
from app.api.notification_rules import router as notification_rules_router
from app.api.session_recordings import router as session_recordings_router
from app.api.sessions.routes import router as sessions_router
from app.api.ssh_certificate_authorities import router as ssh_ca_router
from app.api.ssh_certificates import router as ssh_certificates_router
from app.api.tenancy.routes import router as tenancy_router
from app.api.webhook_endpoints import router as webhook_endpoints_router
from app.api.workflows.routes import router as workflows_router
from app.core.config import Settings
from app.core.database import create_database_engines, get_db, get_read_db

DB_BACKED_GET_ROUTE_ROUTING_INVENTORY = {
    ("GET", "/accounts/"),
    ("GET", "/accounts/{account_id}/rotations"),
    ("GET", "/assets/"),
    ("GET", "/assets/platforms"),
    ("GET", "/assets/{asset_id}"),
    ("GET", "/auth/me"),
    ("GET", "/automation/jobs/runs"),
    ("GET", "/connectors/"),
    ("GET", "/notification-deliveries/"),
    ("GET", "/notification-rules/"),
    ("GET", "/session-recordings/{recording_id}/commands"),
    ("GET", "/session-recordings/commands"),
    ("GET", "/sessions/"),
    ("GET", "/ssh-certificate-authorities/"),
    ("GET", "/ssh-certificate-authorities/trust-bundle"),
    ("GET", "/ssh-certificates/"),
    ("GET", "/tenancy/organizations"),
    ("GET", "/tenancy/projects"),
    ("GET", "/tenancy/teams"),
    ("GET", "/webhook-endpoints/"),
    ("GET", "/workflows/approval-policies"),
    ("GET", "/workflows/grants/active"),
    ("GET", "/workflows/requests"),
    ("GET", "/workflows/requests/{request_id}"),
}

DB_FREE_GET_ROUTE_ROUTING_INVENTORY = {
    ("GET", "/api/v1/audits/events"),
    ("GET", "/api/v1/audits/reports/compliance"),
    ("GET", "/api/v1/audits/reports/summary"),
}

GET_ROUTE_ROUTING_INVENTORY = (
    DB_BACKED_GET_ROUTE_ROUTING_INVENTORY | DB_FREE_GET_ROUTE_ROUTING_INVENTORY
)

ROUTERS_WITH_GET_ROUTES = [
    accounts_router,
    assets_router,
    audits_router,
    auth_router,
    automation_router,
    connectors_router,
    notification_deliveries_router,
    notification_rules_router,
    session_recordings_router,
    sessions_router,
    ssh_ca_router,
    ssh_certificates_router,
    tenancy_router,
    webhook_endpoints_router,
    workflows_router,
]


def test_all_get_routes_are_classified_in_database_routing_inventory() -> None:
    assert _all_get_route_keys() == GET_ROUTE_ROUTING_INVENTORY


def test_audit_read_routes_are_explicitly_db_free_until_audit_store_is_persisted() -> None:
    for method, path in DB_FREE_GET_ROUTE_ROUTING_INVENTORY:
        dependencies = _route_dependency_calls(router=audits_router, method=method, path=path)
        assert get_db not in dependencies
        assert get_read_db not in dependencies


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


def test_connector_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/connectors/"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(router=connectors_router, method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_connector_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/connectors/"),
        ("POST", "/connectors/{connector_id}/heartbeat"),
        ("POST", "/connectors/{connector_id}/rotate-key"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(router=connectors_router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_ssh_ca_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        (ssh_ca_router, "GET", "/ssh-certificate-authorities/"),
        (ssh_ca_router, "GET", "/ssh-certificate-authorities/trust-bundle"),
        (ssh_certificates_router, "GET", "/ssh-certificates/"),
    ]

    for router, method, path in read_routes:
        dependencies = _route_dependency_calls(router=router, method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_ssh_ca_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        (ssh_ca_router, "POST", "/ssh-certificate-authorities/"),
        (ssh_ca_router, "POST", "/ssh-certificate-authorities/{authority_id}/disable"),
        (ssh_certificates_router, "POST", "/ssh-certificates/"),
        (ssh_certificates_router, "POST", "/ssh-certificates/{certificate_id}/revoke"),
    ]

    for router, method, path in write_routes:
        dependencies = _route_dependency_calls(router=router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_automation_job_run_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/automation/jobs/runs"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(router=automation_router, method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_automation_job_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/automation/jobs/credential-rotations"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(router=automation_router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_auth_me_read_route_uses_read_database_dependency() -> None:
    dependencies = _route_dependency_calls(
        router=auth_router, method="GET", path="/auth/me", recursive=True
    )

    assert get_read_db in dependencies
    assert get_db not in dependencies


def test_auth_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/auth/login"),
        ("POST", "/auth/login/2fa"),
        ("POST", "/auth/token/refresh"),
        ("POST", "/auth/2fa/setup"),
        ("POST", "/auth/2fa/verify"),
        ("POST", "/auth/2fa/disable"),
        ("POST", "/auth/password/change"),
        ("POST", "/auth/apikeys"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(router=auth_router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_session_list_read_route_uses_read_service_dependency() -> None:
    dependency_names = _route_dependency_names(
        router=sessions_router, method="GET", path="/sessions/"
    )

    assert "get_read_session_gateway_service" in dependency_names
    assert "get_session_gateway_service" not in dependency_names


def test_session_write_routes_keep_writer_service_dependency() -> None:
    write_routes = [
        ("POST", "/sessions/connection-token"),
        ("POST", "/sessions/"),
        ("POST", "/sessions/{session_id}/close"),
    ]

    for method, path in write_routes:
        dependency_names = _route_dependency_names(
            router=sessions_router, method=method, path=path
        )
        assert "get_session_gateway_service" in dependency_names
        assert "get_read_session_gateway_service" not in dependency_names


def test_workflow_approval_policy_read_routes_use_read_database_dependency() -> None:
    read_routes = [
        ("GET", "/workflows/approval-policies"),
    ]

    for method, path in read_routes:
        dependencies = _route_dependency_calls(router=workflows_router, method=method, path=path)
        assert get_read_db in dependencies
        assert get_db not in dependencies


def test_workflow_approval_policy_write_routes_keep_writer_database_dependency() -> None:
    write_routes = [
        ("POST", "/workflows/approval-policies"),
        ("POST", "/workflows/approval-policies/{policy_id}/versions"),
        ("POST", "/workflows/approval-policies/{policy_id}/rollback"),
    ]

    for method, path in write_routes:
        dependencies = _route_dependency_calls(router=workflows_router, method=method, path=path)
        assert get_db in dependencies
        assert get_read_db not in dependencies


def test_workflow_request_read_routes_use_read_service_dependency() -> None:
    read_routes = [
        ("GET", "/workflows/requests"),
        ("GET", "/workflows/requests/{request_id}"),
        ("GET", "/workflows/grants/active"),
    ]

    for method, path in read_routes:
        dependency_names = _route_dependency_names(
            router=workflows_router, method=method, path=path
        )
        assert "get_read_workflow_service" in dependency_names
        assert "get_workflow_service" not in dependency_names


def test_workflow_request_write_routes_keep_writer_service_dependency() -> None:
    write_routes = [
        ("POST", "/workflows/requests"),
        ("POST", "/workflows/requests/{request_id}/submit"),
        ("POST", "/workflows/requests/{request_id}/approve"),
        ("POST", "/workflows/requests/{request_id}/reject"),
        ("POST", "/workflows/requests/{request_id}/revoke"),
    ]

    for method, path in write_routes:
        dependency_names = _route_dependency_names(
            router=workflows_router, method=method, path=path
        )
        assert "get_workflow_service" in dependency_names
        assert "get_read_workflow_service" not in dependency_names


def _route_dependency_calls(
    *, router: Any = assets_router, method: str, path: str, recursive: bool = False
) -> set[Any]:
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == path and method in route.methods:
            dependencies = route.dependant.dependencies
            if recursive:
                return _dependency_calls_recursive(dependencies)
            return {dependency.call for dependency in dependencies}
    raise AssertionError(f"Route not found: {method} {path}")


def _route_dependency_names(*, router: Any, method: str, path: str) -> set[str]:
    return {
        getattr(call, "__name__", repr(call))
        for call in _route_dependency_calls(router=router, method=method, path=path)
    }


def _dependency_calls_recursive(dependencies: Any) -> set[Any]:
    calls: set[Any] = set()
    stack = list(dependencies)
    while stack:
        dependency = stack.pop()
        calls.add(dependency.call)
        stack.extend(dependency.dependencies)
    return calls


def _all_get_route_keys() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for router in ROUTERS_WITH_GET_ROUTES:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            if "GET" in route.methods:
                routes.add(("GET", route.path))
    return routes
