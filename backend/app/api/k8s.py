"""#t68 K8s 容器纳管 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.k8s_schemas import K8sClusterResponse, K8sClusterUpsert
from app.core.database import get_db, get_read_db
from app.core.deps import current_user
from app.k8s.service import get_cluster_by_asset, upsert_cluster
from app.k8s.validation import load_namespaces
from app.tenancy.scope import actor_scope_from_user

router = APIRouter(prefix="/k8s", tags=["K8s 容器纳管"])


def _require_assets_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


@router.get("/clusters/{asset_id}", response_model=K8sClusterResponse)
async def get_k8s_cluster(
    asset_id: int,
    db: AsyncSession = Depends(get_read_db),
    user: dict = Depends(current_user),
) -> K8sClusterResponse:
    _require_assets_permission(user, "assets:read")
    scope = actor_scope_from_user(user)
    cluster = await get_cluster_by_asset(db, scope, asset_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="K8S_CLUSTER_NOT_FOUND")
    return K8sClusterResponse(
        asset_id=cluster.asset_id,
        tenant_id=cluster.tenant_id,
        api_server=cluster.api_server,
        namespaces=load_namespaces(cluster.namespaces_json),
        has_server_ca=bool(cluster.server_ca_pem.strip()),
    )


@router.put("/clusters/{asset_id}", response_model=K8sClusterResponse)
async def upsert_k8s_cluster(
    asset_id: int,
    body: K8sClusterUpsert,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(current_user),
) -> K8sClusterResponse:
    _require_assets_permission(user, "assets:write")
    scope = actor_scope_from_user(user)
    try:
        cluster = await upsert_cluster(
            db,
            scope,
            asset_id=asset_id,
            api_server=body.api_server,
            server_ca_pem=body.server_ca_pem,
            namespaces=body.namespaces,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return K8sClusterResponse(
        asset_id=cluster.asset_id,
        tenant_id=cluster.tenant_id,
        api_server=cluster.api_server,
        namespaces=load_namespaces(cluster.namespaces_json),
        has_server_ca=bool(cluster.server_ca_pem.strip()),
    )
