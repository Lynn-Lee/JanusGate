"""#t68 K8s 集群与 namespace 作用域服务。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.k8s.validation import (
    intersect_namespaces,
    load_namespaces,
    validate_api_server,
    validate_k8s_name,
    validate_server_ca,
    validate_token_ttl,
)
from app.models.account import Account
from app.models.asset import Asset
from app.models.k8s_cluster import K8sClusterModel
from app.protocols.catalog import CRED_TOKEN, PROTOCOL_BY_ID
from app.tenancy.scope import ActorScope, scoped_select


async def get_cluster_by_asset(
    db: AsyncSession, scope: ActorScope, asset_id: int
) -> K8sClusterModel | None:
    result = await db.execute(
        scoped_select(K8sClusterModel, scope).where(K8sClusterModel.asset_id == asset_id)
    )
    return result.scalar_one_or_none()


async def upsert_cluster(
    db: AsyncSession,
    scope: ActorScope,
    *,
    asset_id: int,
    api_server: str,
    server_ca_pem: str,
    namespaces: list[str],
) -> K8sClusterModel:
    validate_api_server(api_server)
    validate_server_ca(server_ca_pem)
    for ns in namespaces:
        validate_k8s_name(ns, field="namespace")

    asset = await db.execute(
        scoped_select(Asset, scope).where(Asset.id == asset_id)
    )
    row_asset = asset.scalar_one_or_none()
    if row_asset is None or row_asset.asset_type != "cloud":
        raise LookupError("K8S_ASSET_NOT_FOUND")

    existing = await get_cluster_by_asset(db, scope, asset_id)
    if existing is None:
        cluster = K8sClusterModel(
            tenant_id=scope.tenant_id,
            asset_id=asset_id,
            api_server=api_server.strip(),
            server_ca_pem=server_ca_pem.strip(),
            namespaces_json=json.dumps(namespaces),
        )
        db.add(cluster)
    else:
        existing.api_server = api_server.strip()
        existing.server_ca_pem = server_ca_pem.strip()
        existing.namespaces_json = json.dumps(namespaces)
        cluster = existing
    await db.commit()
    await db.refresh(cluster)
    return cluster


def validate_k8s_account_fields(
    *,
    protocol: str,
    secret_id: str,
    k8s_namespaces: list[str],
    k8s_service_account: str,
    k8s_token_ttl_seconds: int,
) -> None:
    if protocol != "k8s":
        return
    definition = PROTOCOL_BY_ID.get("k8s")
    if definition is None or CRED_TOKEN not in definition.credential_types:
        raise ValueError("K8S_PROTOCOL_INVALID")
    if not secret_id.strip():
        raise ValueError("K8S_TOKEN_SECRET_REQUIRED")
    if not k8s_namespaces:
        raise ValueError("K8S_ACCOUNT_NAMESPACES_REQUIRED")
    validate_k8s_name(k8s_service_account, field="service_account")
    validate_token_ttl(k8s_token_ttl_seconds)


def resolve_namespace_scope(
    cluster: K8sClusterModel, account: Account
) -> frozenset[str]:
    cluster_ns = load_namespaces(cluster.namespaces_json)
    account_ns = load_namespaces(account.k8s_namespaces_json)
    return intersect_namespaces(cluster_ns, account_ns)


def pick_namespace(scope: frozenset[str], preferred: str | None = None) -> str:
    if preferred and preferred in scope:
        return preferred
    return sorted(scope)[0]
