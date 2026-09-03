"""Phase 4 automation worker queue contract tests."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.automation_worker import AutomationJobQueue, AutomationWorker


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


class RecordingRedisConsumer(RecordingRedisStream):
    def __init__(self) -> None:
        super().__init__()
        self.acked: list[tuple[str, str, str]] = []

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        assert groupname == "janusgate-workers"
        assert consumername == "worker-1"
        assert streams == {"janusgate:automation": ">"}
        assert count == 10
        assert block == 1000
        return [
            (
                "janusgate:automation",
                [
                    (
                        "1700000000000-0",
                        {
                            "tenant_id": "tenant-a",
                            "job_type": "asset.scan",
                            "requested_by": "user-1",
                            "payload_json": '{"asset_id":"asset-1"}',
                            "payload_format": "json",
                        },
                    )
                ],
            )
        ]

    async def xack(self, name: str, groupname: str, message_id: str) -> int:
        self.acked.append((name, groupname, message_id))
        return 1


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


@pytest.mark.asyncio
async def test_account_change_secret_queue_rejects_password_payload() -> None:
    queue = AutomationJobQueue(redis=RecordingRedisStream())
    with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
        await queue.enqueue(
            tenant_id="tenant-a",
            job_type="account.change_secret",
            requested_by="user-1",
            payload={"account_id": 1, "password": "plain-secret"},
        )


@pytest.mark.asyncio
async def test_account_job_types_enqueue_json_ids_only() -> None:
    stream = RecordingRedisStream()
    queue = AutomationJobQueue(redis=stream)
    job_id = await queue.enqueue(
        tenant_id="tenant-a",
        job_type="account.verify",
        requested_by="user-1",
        payload={"account_id": 1},
    )
    assert job_id == "1700000000000-0"
    _name, fields, _maxlen = stream.calls[0]
    assert fields["job_type"] == "account.verify"
    assert json.loads(fields["payload_json"]) == {"account_id": 1}
    assert fields["payload_format"] == "json"


@pytest.mark.asyncio
async def test_automation_worker_dispatches_json_stream_message_and_acks() -> None:
    stream = RecordingRedisConsumer()
    handled: list[dict[str, Any]] = []

    async def handle_asset_scan(
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, Any],
        message_id: str,
    ) -> None:
        handled.append(
            {
                "tenant_id": tenant_id,
                "requested_by": requested_by,
                "payload": payload,
                "message_id": message_id,
            }
        )

    worker = AutomationWorker(
        redis=stream,
        stream_name="janusgate:automation",
        group_name="janusgate-workers",
        consumer_name="worker-1",
        handlers={"asset.scan": handle_asset_scan},
    )

    processed = await worker.run_once(count=10, block_ms=1000)

    assert processed == 1
    assert handled == [
        {
            "tenant_id": "tenant-a",
            "requested_by": "user-1",
            "payload": {"asset_id": "asset-1"},
            "message_id": "1700000000000-0",
        }
    ]
    assert stream.acked == [
        ("janusgate:automation", "janusgate-workers", "1700000000000-0")
    ]
