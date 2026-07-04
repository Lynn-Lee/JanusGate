"""Automation worker queue foundation.

The Phase 4 automation worker uses a JSON-only Redis Streams style contract.
It intentionally avoids Celery/pickle-style arbitrary Python object dispatch.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping
from typing import Protocol

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

ALLOWED_JOB_TYPES: frozenset[str] = frozenset(
    {"asset.scan", "credential.rotate", "ansible.playbook"}
)
SENSITIVE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "connection_string",
        "credential",
        "credential_value",
        "database_url",
        "dsn",
        "password",
        "plain_secret",
        "plaintext",
        "private_key",
        "refresh_token",
        "secret",
        "signing_secret",
        "token",
    }
)


class RedisStreamClient(Protocol):
    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str: ...


class RedisStreamConsumerClient(RedisStreamClient, Protocol):
    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]: ...

    async def xack(self, name: str, groupname: str, message_id: str) -> int: ...


class AutomationJobHandler(Protocol):
    def __call__(
        self,
        *,
        tenant_id: str,
        requested_by: str,
        payload: dict[str, JsonValue],
        message_id: str,
    ) -> Awaitable[None]: ...


class AutomationJobQueue:
    def __init__(
        self,
        *,
        redis: RedisStreamClient,
        stream_name: str = "janusgate:automation:jobs",
        max_stream_length: int = 10_000,
    ) -> None:
        self._redis = redis
        self._stream_name = stream_name
        self._max_stream_length = max_stream_length

    async def enqueue(
        self,
        *,
        tenant_id: str,
        job_type: str,
        requested_by: str,
        payload: Mapping[str, JsonValue],
    ) -> str:
        if job_type not in ALLOWED_JOB_TYPES:
            raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")
        _assert_no_sensitive_payload_keys(payload)

        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return await self._redis.xadd(
            self._stream_name,
            {
                "tenant_id": tenant_id,
                "job_type": job_type,
                "requested_by": requested_by,
                "payload_json": payload_json,
                "payload_format": "json",
            },
            maxlen=self._max_stream_length,
            approximate=True,
        )


class AutomationWorker:
    def __init__(
        self,
        *,
        redis: RedisStreamConsumerClient,
        handlers: Mapping[str, AutomationJobHandler],
        stream_name: str = "janusgate:automation:jobs",
        group_name: str = "janusgate-automation-workers",
        consumer_name: str = "janusgate-worker",
    ) -> None:
        self._redis = redis
        self._handlers = handlers
        self._stream_name = stream_name
        self._group_name = group_name
        self._consumer_name = consumer_name

    async def run_once(self, *, count: int = 10, block_ms: int = 1000) -> int:
        batches = await self._redis.xreadgroup(
            self._group_name,
            self._consumer_name,
            {self._stream_name: ">"},
            count=count,
            block=block_ms,
        )
        processed = 0
        for stream_name, messages in batches:
            for message_id, fields in messages:
                job_type = fields["job_type"]
                if job_type not in ALLOWED_JOB_TYPES:
                    raise ValueError("UNSUPPORTED_AUTOMATION_JOB_TYPE")
                handler = self._handlers.get(job_type)
                if handler is None:
                    raise ValueError("AUTOMATION_JOB_HANDLER_NOT_CONFIGURED")
                if fields.get("payload_format") != "json":
                    raise ValueError("UNSUPPORTED_AUTOMATION_JOB_PAYLOAD_FORMAT")
                payload = json.loads(fields["payload_json"])
                if not isinstance(payload, dict):
                    raise ValueError("AUTOMATION_JOB_PAYLOAD_MUST_BE_OBJECT")
                await handler(
                    tenant_id=fields["tenant_id"],
                    requested_by=fields["requested_by"],
                    payload=payload,
                    message_id=message_id,
                )
                await self._redis.xack(stream_name, self._group_name, message_id)
                processed += 1
        return processed


def _assert_no_sensitive_payload_keys(payload: Mapping[str, JsonValue]) -> None:
    for key, value in payload.items():
        if key.lower() in SENSITIVE_PAYLOAD_KEYS:
            raise ValueError("AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET")
        if isinstance(value, dict):
            _assert_no_sensitive_payload_keys(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _assert_no_sensitive_payload_keys(item)
