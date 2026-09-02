"""#t63 RBAC 辅助：ID 生成与 JSON 序列化。"""

from __future__ import annotations

import json
from uuid import uuid4


def new_role_id() -> str:
    return f"role_{uuid4().hex}"


def new_binding_id() -> str:
    return f"rb_{uuid4().hex}"


def new_object_permission_id() -> str:
    return f"rop_{uuid4().hex}"


def dump_json_list(values: list[str]) -> str:
    return json.dumps(values)


def load_json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]
