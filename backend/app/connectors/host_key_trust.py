"""主机密钥审批状态：未知确认 vs 密钥变更警告，fail-closed，绝不 TOFU。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.ssh_hostkey import HostKeyScan, scan_host_key
from app.models.asset import Asset
from app.models.host_key import (
    AssetHostKeyModel,
    HostKeyPresentation,
    HostKeyTrustStatus,
)

HOST_KEY_UNKNOWN_TITLE = "确认这台主机"
HOST_KEY_CHANGED_TITLE = "这台主机的密钥变了"
CONNECT_DENIED_COPY = "无法连接"

SSH_CONNECT_PROTOCOLS = frozenset({"ssh", "sftp", "exec", "interactive"})


class HostKeyScanner(Protocol):
    async def scan(self, host: str, port: int) -> HostKeyScan:
        """采集主机公钥，不信任、不落库。"""


ScanFn = Callable[[str, int], Awaitable[HostKeyScan]]


@dataclass(frozen=True)
class HostKeyClassification:
    state: HostKeyPresentation
    title: str
    public_key: str
    fingerprint: str
    previous_fingerprint: str = ""

    def as_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": self.state.value,
            "title": self.title,
            "public_key": self.public_key,
            "fingerprint": self.fingerprint,
        }
        if self.previous_fingerprint:
            payload["previous_fingerprint"] = self.previous_fingerprint
        return payload


class AsyncScanAdapter:
    def __init__(self, scan: ScanFn | None = None) -> None:
        self._scan = scan or (lambda host, port: scan_host_key(host, port))

    async def scan(self, host: str, port: int) -> HostKeyScan:
        return await self._scan(host, port)


def _normalize_key(value: str) -> str:
    return " ".join(value.split())


def classify_presented_key(
    *,
    approved_public_key: str,
    presented: HostKeyScan,
) -> HostKeyClassification:
    """把采集到的公钥与已批准记录比较：无记录=未知，不匹配=变更。"""

    approved = _normalize_key(approved_public_key)
    presented_key = _normalize_key(presented.public_key)
    if not approved:
        return HostKeyClassification(
            state=HostKeyPresentation.UNKNOWN,
            title=HOST_KEY_UNKNOWN_TITLE,
            public_key=presented.public_key,
            fingerprint=presented.fingerprint,
        )
    if approved == presented_key:
        return HostKeyClassification(
            state=HostKeyPresentation.APPROVED,
            title="",
            public_key=presented.public_key,
            fingerprint=presented.fingerprint,
        )
    return HostKeyClassification(
        state=HostKeyPresentation.CHANGED,
        title=HOST_KEY_CHANGED_TITLE,
        public_key=presented.public_key,
        fingerprint=presented.fingerprint,
        previous_fingerprint="",
    )


class HostKeyTrustStore:
    """资产主机密钥信任库：只有审批通过的公钥可用于连接。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session(self, db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
        if db is not None:
            yield db
            return
        if self._session_factory is None:
            raise RuntimeError("HOST_KEY_STORE_SESSION_MISSING")
        async with self._session_factory() as session:
            yield session

    async def get(
        self, *, tenant_id: str, asset_id: str, db: AsyncSession | None = None
    ) -> AssetHostKeyModel | None:
        async with self._session(db) as session:
            return await _load_row(session, tenant_id=tenant_id, asset_id=asset_id)

    async def approved_public_key(
        self, *, tenant_id: str, asset_id: str, db: AsyncSession | None = None
    ) -> str:
        row = await self.get(tenant_id=tenant_id, asset_id=asset_id, db=db)
        if row is None:
            return ""
        return row.approved_public_key.strip()

    async def record_pending(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        host: str,
        port: int,
        classification: HostKeyClassification,
        workflow_request_id: str = "",
        db: AsyncSession | None = None,
    ) -> None:
        """只记下待审批公钥，绝不写入 approved（禁止 TOFU）。"""

        async with self._session(db) as session:
            row = await _load_or_create(session, tenant_id=tenant_id, asset_id=asset_id)
            row.host = host
            row.port = port
            row.pending_public_key = classification.public_key
            row.pending_fingerprint = classification.fingerprint
            row.pending_state = classification.state.value
            row.pending_status = HostKeyTrustStatus.PENDING.value
            if workflow_request_id:
                row.workflow_request_id = workflow_request_id
            if db is None:
                await session.commit()
            else:
                await session.flush()

    async def approve_presented(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        public_key: str,
        fingerprint: str,
        host: str = "",
        port: int = 22,
        workflow_request_id: str = "",
        db: AsyncSession | None = None,
    ) -> None:
        async with self._session(db) as session:
            row = await _load_or_create(session, tenant_id=tenant_id, asset_id=asset_id)
            if host:
                row.host = host
            if port:
                row.port = port
            row.approved_public_key = public_key
            row.approved_fingerprint = fingerprint
            row.pending_public_key = ""
            row.pending_fingerprint = ""
            row.pending_state = ""
            row.pending_status = ""
            if workflow_request_id:
                row.workflow_request_id = workflow_request_id
            if db is None:
                await session.commit()
            else:
                await session.flush()

    async def reject_presented(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        public_key: str = "",
        fingerprint: str = "",
        workflow_request_id: str = "",
        db: AsyncSession | None = None,
    ) -> None:
        async with self._session(db) as session:
            row = await _load_or_create(session, tenant_id=tenant_id, asset_id=asset_id)
            if public_key:
                row.pending_public_key = public_key
            if fingerprint:
                row.pending_fingerprint = fingerprint
            row.pending_status = HostKeyTrustStatus.REJECTED.value
            if workflow_request_id:
                row.workflow_request_id = workflow_request_id
            if db is None:
                await session.commit()
            else:
                await session.flush()


class HostKeyTrustService:
    """扫描 → 分类 → 叠到现有审批 metadata；审批通过才固定公钥。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        store: HostKeyTrustStore | None = None,
        scanner: HostKeyScanner | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._store = store or HostKeyTrustStore(session_factory)
        self._scanner = scanner or AsyncScanAdapter()

    @property
    def store(self) -> HostKeyTrustStore:
        return self._store

    async def classify_asset(
        self, *, tenant_id: str, asset_id: str, db: AsyncSession | None = None
    ) -> HostKeyClassification | None:
        asset = await self._load_asset(tenant_id=tenant_id, asset_id=asset_id, db=db)
        if asset is None:
            return None
        try:
            presented = await self._scanner.scan(asset.address, asset.port)
        except Exception:
            return None
        row = await self._store.get(tenant_id=tenant_id, asset_id=str(asset.id), db=db)
        approved = row.approved_public_key if row is not None else ""
        classification = classify_presented_key(
            approved_public_key=approved, presented=presented
        )
        if row is not None and classification.state is HostKeyPresentation.CHANGED:
            classification = HostKeyClassification(
                state=classification.state,
                title=HOST_KEY_CHANGED_TITLE,
                public_key=classification.public_key,
                fingerprint=classification.fingerprint,
                previous_fingerprint=row.approved_fingerprint,
            )
        return classification

    async def overlay_request_metadata(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        protocol: str,
        metadata: dict[str, Any],
        workflow_request_id: str = "",
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        if protocol.lower() not in SSH_CONNECT_PROTOCOLS:
            return metadata
        classification = await self.classify_asset(
            tenant_id=tenant_id, asset_id=asset_id, db=db
        )
        if classification is None:
            return metadata
        merged = dict(metadata)
        if classification.state is HostKeyPresentation.APPROVED:
            return merged
        asset = await self._load_asset(tenant_id=tenant_id, asset_id=asset_id, db=db)
        if asset is not None:
            await self._store.record_pending(
                tenant_id=tenant_id,
                asset_id=str(asset.id),
                host=asset.address,
                port=asset.port,
                classification=classification,
                workflow_request_id=workflow_request_id,
                db=db,
            )
        merged["host_key"] = classification.as_metadata()
        return merged

    async def apply_workflow_decision(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        approved: bool,
        metadata: dict[str, Any],
        workflow_request_id: str = "",
        db: AsyncSession | None = None,
    ) -> None:
        host_key = metadata.get("host_key")
        if not isinstance(host_key, dict):
            return
        public_key = str(host_key.get("public_key") or "")
        fingerprint = str(host_key.get("fingerprint") or "")
        if not public_key:
            return
        if approved:
            await self._store.approve_presented(
                tenant_id=tenant_id,
                asset_id=str(asset_id),
                public_key=public_key,
                fingerprint=fingerprint,
                workflow_request_id=workflow_request_id,
                db=db,
            )
            return
        await self._store.reject_presented(
            tenant_id=tenant_id,
            asset_id=str(asset_id),
            public_key=public_key,
            fingerprint=fingerprint,
            workflow_request_id=workflow_request_id,
            db=db,
        )

    async def _load_asset(
        self, *, tenant_id: str, asset_id: str, db: AsyncSession | None = None
    ) -> Asset | None:
        async with self._store._session(db) as session:
            return await _get_tenant_asset(session, tenant_id=tenant_id, asset_id=asset_id)


async def _load_row(
    session: AsyncSession, *, tenant_id: str, asset_id: str
) -> AssetHostKeyModel | None:
    result = await session.execute(
        select(AssetHostKeyModel)
        .where(AssetHostKeyModel.tenant_id == tenant_id)
        .where(AssetHostKeyModel.asset_id == str(asset_id))
    )
    return result.scalar_one_or_none()


async def _load_or_create(
    session: AsyncSession, *, tenant_id: str, asset_id: str
) -> AssetHostKeyModel:
    row = await _load_row(session, tenant_id=tenant_id, asset_id=asset_id)
    if row is not None:
        return row
    row = AssetHostKeyModel(
        id=f"hk_{uuid4().hex}",
        tenant_id=tenant_id,
        asset_id=str(asset_id),
    )
    session.add(row)
    await session.flush()
    return row


async def _get_tenant_asset(
    session: AsyncSession, *, tenant_id: str, asset_id: str
) -> Asset | None:
    try:
        numeric_id = int(asset_id)
    except ValueError:
        return None
    result = await session.execute(
        select(Asset).where(Asset.id == numeric_id).where(Asset.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()
