"""#t79 租户动态配置：白名单键、校验与变更审计。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import TenantSetting, TenantSettingRevision, UserPreference

SettingValue = bool | int | str

SETTING_SPECS: dict[str, dict[str, Any]] = {
    "session_idle_timeout_minutes": {
        "value_type": "int",
        "minimum": 1,
        "maximum": 1440,
        "default": 30,
    },
    "password_min_length": {
        "value_type": "int",
        "minimum": 8,
        "maximum": 128,
        "default": 8,
    },
    "weak_password_check_enabled": {
        "value_type": "bool",
        "default": True,
    },
    "ui_timezone": {
        "value_type": "str",
        "max_length": 64,
        "default": "Asia/Singapore",
    },
}

PREFERENCE_SPECS: dict[str, dict[str, Any]] = {
    "locale": {
        "value_type": "str",
        "choices": ("zh-CN", "en-US"),
        "default": "zh-CN",
    },
    "page_size": {
        "value_type": "int",
        "minimum": 10,
        "maximum": 100,
        "default": 20,
    },
    "theme": {
        "value_type": "str",
        "choices": ("light", "dark", "system"),
        "default": "light",
    },
}


def dump_setting_value(value: SettingValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def parse_setting_value(raw: str) -> SettingValue:
    parsed: Any = json.loads(raw)
    if isinstance(parsed, bool | int | str):
        return parsed
    raise ValueError("SETTING_VALUE_INVALID")


def coerce_spec_value(spec: dict[str, Any], value: Any) -> SettingValue:
    value_type = spec["value_type"]
    if value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError("SETTING_VALUE_INVALID")
        return value
    if value_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("SETTING_VALUE_INVALID")
        minimum = int(spec.get("minimum", value))
        maximum = int(spec.get("maximum", value))
        if value < minimum or value > maximum:
            raise ValueError("SETTING_VALUE_OUT_OF_RANGE")
        return value
    if not isinstance(value, str):
        raise ValueError("SETTING_VALUE_INVALID")
    text = value.strip()
    if not text:
        raise ValueError("SETTING_VALUE_INVALID")
    max_length = int(spec.get("max_length", 256))
    if len(text) > max_length:
        raise ValueError("SETTING_VALUE_INVALID")
    choices = spec.get("choices")
    if choices is not None and text not in choices:
        raise ValueError("SETTING_VALUE_INVALID")
    return text


async def load_setting_map(db: AsyncSession, tenant_id: str) -> dict[str, SettingValue]:
    """返回白名单键的有效配置，缺省用默认值。"""

    result = await db.execute(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id))
    stored = {row.key: row for row in result.scalars().all()}
    values: dict[str, SettingValue] = {}
    for key, spec in SETTING_SPECS.items():
        row = stored.get(key)
        if row is None:
            values[key] = spec["default"]
            continue
        try:
            values[key] = coerce_spec_value(spec, parse_setting_value(row.value_json))
        except (ValueError, json.JSONDecodeError):
            values[key] = spec["default"]
    return values


async def password_min_length_for_tenant(db: AsyncSession, tenant_id: str) -> int:
    settings = await load_setting_map(db, tenant_id)
    return int(settings["password_min_length"])


async def weak_password_check_enabled(db: AsyncSession, tenant_id: str) -> bool:
    settings = await load_setting_map(db, tenant_id)
    return bool(settings["weak_password_check_enabled"])


async def upsert_settings(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor_id: str,
    updates: dict[str, Any],
) -> dict[str, SettingValue]:
    if not updates:
        return await load_setting_map(db, tenant_id)
    unknown = set(updates) - set(SETTING_SPECS)
    if unknown:
        raise ValueError("SETTING_KEY_NOT_ALLOWED")
    current = await load_setting_map(db, tenant_id)
    result = await db.execute(select(TenantSetting).where(TenantSetting.tenant_id == tenant_id))
    rows = {row.key: row for row in result.scalars().all()}
    for key, raw_value in updates.items():
        spec = SETTING_SPECS[key]
        new_value = coerce_spec_value(spec, raw_value)
        old_value = current[key]
        encoded = dump_setting_value(new_value)
        row = rows.get(key)
        if row is None:
            row = TenantSetting(
                tenant_id=tenant_id,
                key=key,
                value_json=encoded,
                updated_by=actor_id,
            )
            db.add(row)
        else:
            row.value_json = encoded
            row.updated_by = actor_id
        if old_value != new_value:
            db.add(
                TenantSettingRevision(
                    tenant_id=tenant_id,
                    key=key,
                    old_value_json=dump_setting_value(old_value),
                    new_value_json=encoded,
                    actor_id=actor_id,
                )
            )
    await db.commit()
    return await load_setting_map(db, tenant_id)


def setting_items(values: dict[str, SettingValue]) -> list[dict[str, Any]]:
    return [{"key": key, "value": values[key]} for key in SETTING_SPECS]


async def load_preference_map(
    db: AsyncSession, *, tenant_id: str, user_id: str
) -> dict[str, SettingValue]:
    result = await db.execute(
        select(UserPreference).where(
            UserPreference.tenant_id == tenant_id,
            UserPreference.user_id == user_id,
        )
    )
    stored = {row.key: row for row in result.scalars().all()}
    values: dict[str, SettingValue] = {}
    for key, spec in PREFERENCE_SPECS.items():
        row = stored.get(key)
        if row is None:
            values[key] = spec["default"]
            continue
        try:
            values[key] = coerce_spec_value(spec, parse_setting_value(row.value_json))
        except (ValueError, json.JSONDecodeError):
            values[key] = spec["default"]
    return values


async def upsert_preferences(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    updates: dict[str, Any],
) -> dict[str, SettingValue]:
    if not updates:
        return await load_preference_map(db, tenant_id=tenant_id, user_id=user_id)
    unknown = set(updates) - set(PREFERENCE_SPECS)
    if unknown:
        raise ValueError("PREFERENCE_KEY_NOT_ALLOWED")
    result = await db.execute(
        select(UserPreference).where(
            UserPreference.tenant_id == tenant_id,
            UserPreference.user_id == user_id,
        )
    )
    rows = {row.key: row for row in result.scalars().all()}
    for key, raw_value in updates.items():
        spec = PREFERENCE_SPECS[key]
        new_value = coerce_spec_value(spec, raw_value)
        encoded = dump_setting_value(new_value)
        row = rows.get(key)
        if row is None:
            db.add(
                UserPreference(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    key=key,
                    value_json=encoded,
                )
            )
        else:
            row.value_json = encoded
    await db.commit()
    return await load_preference_map(db, tenant_id=tenant_id, user_id=user_id)


def preference_items(values: dict[str, SettingValue]) -> list[dict[str, Any]]:
    return [{"key": key, "value": values[key]} for key in PREFERENCE_SPECS]
