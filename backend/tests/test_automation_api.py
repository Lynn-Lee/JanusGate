"""Phase 4 automation scheduling API contract tests."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core.deps import current_user, get_redis
from app.main import app


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


def test_asset_scan_scheduling_api_enqueues_tenant_scoped_job() -> None:
    stream = RecordingRedisStream()
    app.dependency_overrides[get_redis] = lambda: stream

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:write"])
        response = client.post(
            "/api/v1/automation/jobs/asset-scans",
            json={"asset_id": 42, "scan_profile": "ssh-baseline"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "1700000000000-0",
        "job_type": "asset.scan",
        "status": "queued",
    }
    assert len(stream.calls) == 1
    stream_name, fields, maxlen = stream.calls[0]
    assert stream_name == "janusgate:automation:jobs"
    assert maxlen == 10_000
    assert fields["tenant_id"] == "tenant-a"
    assert fields["job_type"] == "asset.scan"
    assert fields["requested_by"] == "user-1"
    assert json.loads(fields["payload_json"]) == {
        "asset_id": 42,
        "scan_profile": "ssh-baseline",
    }
    assert "secret" not in json.dumps(fields).lower()


def test_asset_scan_scheduling_api_requires_automation_write_permission() -> None:
    app.dependency_overrides[get_redis] = lambda: RecordingRedisStream()

    with TestClient(app) as client:
        install_user(tenant_id="tenant-a", permissions=["automation:read"])
        response = client.post(
            "/api/v1/automation/jobs/asset-scans",
            json={"asset_id": 42, "scan_profile": "ssh-baseline"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 403
