"""#t77 job center helpers: playbook path jail + target id JSON."""
from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings, settings


def validate_playbook_relative_name(playbook_name: str) -> str:
    """Reject absolute / traversal / non-yaml names before enqueue."""

    name = playbook_name.strip()
    candidate = Path(name)
    if (
        not name
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.suffix not in {".yml", ".yaml"}
    ):
        raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
    return name


def resolve_playbook_file(
    playbook_name: str,
    *,
    playbook_root: Path | None = None,
    app_settings: Settings = settings,
) -> Path:
    """Resolve a relative playbook under ANSIBLE_PLAYBOOK_ROOT (must exist)."""

    safe_name = validate_playbook_relative_name(playbook_name)
    root = (playbook_root or Path(app_settings.ANSIBLE_PLAYBOOK_ROOT)).resolve()
    playbook_path = (root / safe_name).resolve()
    if not playbook_path.is_relative_to(root) or not playbook_path.is_file():
        raise ValueError("ANSIBLE_PLAYBOOK_NOT_ALLOWED")
    return playbook_path


def encode_target_asset_ids(asset_ids: list[int]) -> str:
    return json.dumps(list(asset_ids), separators=(",", ":"))


def decode_target_asset_ids(raw: str) -> list[int]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("JOB_TARGET_ASSET_IDS_INVALID") from exc
    if not isinstance(value, list):
        raise ValueError("JOB_TARGET_ASSET_IDS_INVALID")
    asset_ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ValueError("JOB_TARGET_ASSET_IDS_INVALID")
        asset_ids.append(item)
    return asset_ids
