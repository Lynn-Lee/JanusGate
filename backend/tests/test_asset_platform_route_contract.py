"""Regression coverage for static asset platform routes."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_read_db
from app.core.deps import current_user
from app.main import app
from app.models.asset import Platform


class ScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalars(self) -> ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self.values


class FakeDB:
    async def execute(self, _statement: Any) -> ScalarResult:
        return ScalarResult([
            Platform(id=1, name="Linux", category="host", protocols='["ssh"]', is_active=True)
        ])


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_platform_list_api_contract_uses_static_route() -> None:
    app.dependency_overrides[current_user] = lambda: {"id": 1, "username": "alice", "permissions": ["assets:read"]}
    app.dependency_overrides[get_read_db] = lambda: FakeDB()

    with TestClient(app) as client:
        response = client.get("/api/v1/assets/platforms")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "name": "Linux",
            "category": "host",
            "protocols": '["ssh"]',
            "is_active": True,
        }
    ]
