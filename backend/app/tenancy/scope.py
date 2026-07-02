"""Reusable tenant-scope helpers for DB queries."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, select


@dataclass(frozen=True)
class ActorScope:
    user_id: str
    tenant_id: str
    organization_ids: tuple[str, ...] = ()
    team_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


def actor_scope_from_user(user: dict[str, Any]) -> ActorScope:
    return ActorScope(
        user_id=str(user["id"]),
        tenant_id=str(user.get("tenant_id") or "default"),
        organization_ids=_coerce_scope_ids(user, "organization_id", "organization_ids"),
        team_ids=_coerce_scope_ids(user, "team_id", "team_ids"),
        project_ids=_coerce_scope_ids(user, "project_id", "project_ids"),
        permissions=tuple(str(permission) for permission in user.get("permissions", ())),
    )


def scoped_select[T](model: type[T], actor_scope: ActorScope) -> Select[tuple[T]]:
    tenant_column = getattr(model, "tenant_id", None)
    if tenant_column is None:
        raise ValueError(f"{model.__name__} is not tenant scoped")

    statement = select(model).where(tenant_column == actor_scope.tenant_id)
    if "admin" in actor_scope.permissions:
        return statement

    model_id = getattr(model, "id", None)
    if actor_scope.project_ids and model.__name__ == "Project" and model_id is not None:
        statement = statement.where(model_id.in_(actor_scope.project_ids))
    elif actor_scope.team_ids and model.__name__ == "Team" and model_id is not None:
        statement = statement.where(model_id.in_(actor_scope.team_ids))
    elif actor_scope.organization_ids and model.__name__ == "Organization" and model_id is not None:
        statement = statement.where(model_id.in_(actor_scope.organization_ids))
    return statement


def _coerce_scope_ids(user: dict[str, Any], single_key: str, plural_key: str) -> tuple[str, ...]:
    values = user.get(plural_key)
    if values is None:
        values = [user[single_key]] if user.get(single_key) else []
    return tuple(str(value) for value in values if value)
