"""Phase 4 persistent connector management API routes."""
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.connector_schemas import (
    ConnectorCapability,
    ConnectorCreate,
    ConnectorKeyRotationRequest,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorStatus,
)
from app.core.database import get_db
from app.core.deps import current_user
from app.models.connector import Connector

router = APIRouter(prefix="/connectors", tags=["连接器"])


@router.get("/", response_model=ConnectorListResponse)
async def list_connectors(
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectorListResponse:
    _require_connector_permission(user, "connectors:read")
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(Connector).where(Connector.tenant_id == tenant_id).order_by(Connector.id)
    )
    connectors = result.scalars().all()
    items = [_connector_response(connector) for connector in connectors]
    return ConnectorListResponse(items=items, total=len(items))


@router.post("/", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    data: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectorResponse:
    _require_connector_permission(user, "connectors:write")
    _validate_fingerprint(data.public_key_fingerprint, "INVALID_CONNECTOR_FINGERPRINT")
    if data.mtls_certificate_fingerprint is not None:
        _validate_fingerprint(
            data.mtls_certificate_fingerprint,
            "INVALID_CONNECTOR_MTLS_FINGERPRINT",
        )

    connector = Connector(
        tenant_id=str(user.get("tenant_id") or "default"),
        name=data.name,
        environment=data.environment,
        public_key_fingerprint=data.public_key_fingerprint,
        mtls_certificate_fingerprint=data.mtls_certificate_fingerprint,
        attestation_nonce=data.attestation_nonce,
        attestation_digest=data.attestation_digest,
        capabilities_json=json.dumps([capability.value for capability in data.capabilities]),
        status=data.status.value,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return _connector_response(connector)


@router.post("/{connector_id}/heartbeat", response_model=ConnectorResponse)
async def record_connector_heartbeat(
    connector_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectorResponse:
    _require_connector_permission(user, "connectors:write")
    connector = await _get_scoped_connector(db=db, user=user, connector_id=connector_id)
    if connector.status != "active":
        raise HTTPException(status_code=403, detail="CONNECTOR_NOT_ACTIVE")
    connector.last_heartbeat_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(connector)
    return _connector_response(connector)


@router.post("/{connector_id}/rotate-key", response_model=ConnectorResponse)
async def rotate_connector_key(
    connector_id: int,
    data: ConnectorKeyRotationRequest,
    db: AsyncSession = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> ConnectorResponse:
    _require_connector_permission(user, "connectors:write")
    connector = await _get_scoped_connector(db=db, user=user, connector_id=connector_id)
    if connector.status != "active":
        raise HTTPException(status_code=403, detail="CONNECTOR_NOT_ACTIVE")
    _validate_fingerprint(data.public_key_fingerprint, "INVALID_CONNECTOR_FINGERPRINT")
    connector.previous_public_key_fingerprint = connector.public_key_fingerprint
    connector.public_key_fingerprint = data.public_key_fingerprint
    connector.key_rotated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(connector)
    return _connector_response(connector)


async def _get_scoped_connector(
    *, db: AsyncSession, user: dict[str, Any], connector_id: int
) -> Connector:
    tenant_id = str(user.get("tenant_id") or "default")
    result = await db.execute(
        select(Connector).where(Connector.id == connector_id).where(Connector.tenant_id == tenant_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=404, detail="CONNECTOR_NOT_FOUND")
    return connector


def _require_connector_permission(user: dict[str, Any], permission: str) -> None:
    permissions = user.get("permissions", [])
    if "admin" in permissions or permission in permissions:
        return
    raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")


def _validate_fingerprint(value: str, error_code: str) -> None:
    if not value.startswith("sha256:"):
        raise HTTPException(status_code=400, detail=error_code)


def _connector_response(connector: Connector) -> ConnectorResponse:
    return ConnectorResponse(
        id=connector.id,
        tenant_id=connector.tenant_id,
        name=connector.name,
        environment=connector.environment,
        public_key_fingerprint=connector.public_key_fingerprint,
        previous_public_key_fingerprint=connector.previous_public_key_fingerprint,
        capabilities=_capabilities(connector.capabilities_json),
        status=ConnectorStatus(connector.status),
        mtls_bound=connector.mtls_certificate_fingerprint is not None,
        attestation_bound=connector.attestation_digest is not None,
        registered_at=_as_utc(connector.registered_at),
        last_heartbeat_at=_as_utc(connector.last_heartbeat_at),
        key_rotated_at=_as_utc(connector.key_rotated_at),
    )


def _capabilities(value: str) -> list[ConnectorCapability]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [ConnectorCapability(str(item)) for item in parsed]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
