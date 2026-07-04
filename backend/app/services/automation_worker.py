"""Automation worker queue foundation.

The Phase 4 automation worker uses a JSON-only Redis Streams style contract.
It intentionally avoids Celery/pickle-style arbitrary Python object dispatch.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
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
