"""#t78 命令/录像多后端存储（local / s3 / oss / es），本切片不引入 pickle。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.automation_worker import SENSITIVE_PAYLOAD_KEYS

ALLOWED_STORAGE_KINDS = frozenset({"local", "s3", "oss", "es"})
ALLOWED_PURPOSES = frozenset({"command", "replay"})
REDACTED_CONFIG_KEYS = SENSITIVE_PAYLOAD_KEYS | {"access_key", "secret_key", "access_key_id"}


def validate_storage_config(*, kind: str, purpose: str, config: dict[str, Any]) -> dict[str, Any]:
    """校验存储后端种类，并拒绝凭据类配置键进入明文契约。"""

    if kind not in ALLOWED_STORAGE_KINDS:
        raise ValueError("UNSUPPORTED_STORAGE_BACKEND")
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError("UNSUPPORTED_STORAGE_PURPOSE")
    for key in config:
        if key.lower() in REDACTED_CONFIG_KEYS:
            raise ValueError("STORAGE_CONFIG_CONTAINS_SECRET")
    if kind in {"s3", "oss"} and not str(config.get("bucket") or "").strip():
        raise ValueError("STORAGE_BUCKET_REQUIRED")
    if kind == "es" and not str(config.get("index") or "").strip():
        raise ValueError("STORAGE_INDEX_REQUIRED")
    return dict(config)


def public_storage_config(config: dict[str, Any]) -> dict[str, Any]:
    """列表回显时脱敏可能残留的密钥字段。"""

    return {
        key: "******" if key.lower() in REDACTED_CONFIG_KEYS else value
        for key, value in config.items()
    }


class SessionObjectStore:
    """按 kind 把命令 JSON 或录像字节写入租户隔离目录；s3/oss/es 本切片落本地适配前缀。"""

    def __init__(self, *, root: str | None = None) -> None:
        self._root = Path(root or settings.SESSION_OBJECT_STORAGE_ROOT)

    def put(
        self,
        *,
        tenant_id: str,
        kind: str,
        purpose: str,
        object_id: str,
        payload: bytes,
        config: dict[str, Any],
    ) -> str:
        validate_storage_config(kind=kind, purpose=purpose, config=config)
        path = self._object_path(tenant_id=tenant_id, kind=kind, purpose=purpose, object_id=object_id, config=config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path)

    def get(
        self,
        *,
        tenant_id: str,
        kind: str,
        purpose: str,
        object_id: str,
        config: dict[str, Any],
    ) -> bytes:
        path = self._object_path(tenant_id=tenant_id, kind=kind, purpose=purpose, object_id=object_id, config=config)
        if not path.is_file():
            raise FileNotFoundError("STORAGE_OBJECT_NOT_FOUND")
        return path.read_bytes()

    def search_commands(self, *, tenant_id: str, kind: str, query: str, config: dict[str, Any]) -> list[str]:
        """es 后端按命令全文包含匹配；其它 kind 扫描本地 JSON。"""

        base = self._object_path(tenant_id=tenant_id, kind=kind, purpose="command", object_id="_", config=config).parent
        if not base.exists():
            return []
        hits: list[str] = []
        needle = query.lower()
        for path in base.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            if needle in text.lower():
                hits.append(path.stem)
        return hits

    def _object_path(
        self,
        *,
        tenant_id: str,
        kind: str,
        purpose: str,
        object_id: str,
        config: dict[str, Any],
    ) -> Path:
        bucket = str(config.get("bucket") or config.get("index") or "default")
        suffix = ".json" if purpose == "command" else ".replay"
        safe_id = object_id.replace("/", "_")
        return self._root / tenant_id / kind / bucket / purpose / f"{safe_id}{suffix}"


def dumps_command_event(event: dict[str, Any]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
