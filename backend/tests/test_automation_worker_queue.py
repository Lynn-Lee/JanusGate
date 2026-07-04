"""Phase 4 automation worker queue contract tests."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.automation_worker import AutomationJobQueue


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


@pytest.mark.asyncio
async def test_automation_job_queue_enqueues_json_only_stream_message() -> None:
    stream = RecordingRedisStream()
    queue = AutomationJobQueue(redis=stream, stream_name="janusgate:automation")

    job_id = await queue.enqueue(
        tenant_id="tenant-a",
        job_type="asset.scan",
        requested_by="user-1",
        payload={"asset_id": "asset-1", "scan_profile": "ssh-baseline"},
    )

    assert job_id == "1700000000000-0"
    assert len(stream.calls) == 1
    stream_name, fields, maxlen = stream.calls[0]
    assert stream_name == "janusgate:automation"
    assert maxlen == 10_000
    assert fields["tenant_id"] == "tenant-a"
    assert fields["job_type"] == "asset.scan"
    assert fields["requested_by"] == "user-1"
    assert json.loads(fields["payload_json"]) == {
        "asset_id": "asset-1",
        "scan_profile": "ssh-baseline",
    }
    assert all(not isinstance(value, bytes) for value in fields.values())
    assert "pickle" not in json.dumps(fields).lower()


@pytest.mark.asyncio
async def test_automation_job_queue_rejects_unknown_job_type() -> None:
    queue = AutomationJobQueue(redis=RecordingRedisStream())

    with pytest.raises(ValueError, match="UNSUPPORTED_AUTOMATION_JOB_TYPE"):
        await queue.enqueue(
            tenant_id="tenant-a",
            job_type="arbitrary.python.callable",
            requested_by="user-1",
            payload={},
        )


@pytest.mark.asyncio
async def test_automation_job_queue_rejects_sensitive_payload_fields() -> None:
    queue = AutomationJobQueue(redis=RecordingRedisStream())
    payload: dict[str, Any] = {"asset_id": "asset-1", "password": "plain-secret"}

    with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
        await queue.enqueue(
            tenant_id="tenant-a",
            job_type="credential.rotate",
            requested_by="user-1",
            payload=payload,
        )
