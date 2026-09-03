"""#t68 生产 K8s SessionConnectionResolver：集群注册表 + Vault 审批解包 + TokenRequest。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.k8s_exec import K8sChannelError, K8sCredential, K8sTarget, NamespaceScope
from app.connectors.session_runtime import (
    ConnectorSessionMode,
    K8sConnectionBundle,
    SessionConnectionSpec,
)
from app.k8s.service import get_cluster_by_asset, pick_namespace, resolve_namespace_scope
from app.k8s.token_request import K8sTokenRequestError, request_service_account_token
from app.models.account import Account
from app.models.asset import Asset
from app.models.session import SessionModel
from app.policy.schemas import ApprovalState
from app.tenancy.scope import ActorScope


class K8sSecretUnwrapper(Protocol):
    async def unwrap(self, secret_id: str) -> str:
        """直接解包（仅用于无 JIT grant 的非生产/测试路径）。"""

    async def unwrap_after_approval(self, secret_id: str, approval: ApprovalState | None) -> str:
        """审批后解包 K8s 集群 token（envelope + grant 绑定）。"""


class CallableK8sSecretUnwrapper:
    def __init__(
        self,
        unwrap: Callable[[str], Awaitable[str] | str],
        unwrap_after_approval: Callable[[str, ApprovalState | None], Awaitable[str] | str]
        | None = None,
    ) -> None:
        self._unwrap = unwrap
        after: Callable[[str, ApprovalState | None], Awaitable[str] | str]
        if unwrap_after_approval is None:

            async def _require_not_approved(secret_id: str, approval: ApprovalState | None) -> str:
                if approval is not None and approval.status != "not_required":
                    raise ValueError("SECRET_UNWRAP_APPROVAL_REQUIRED")
                value = unwrap(secret_id)
                if isinstance(value, str):
                    return value
                return await value

            after = _require_not_approved
        else:
            after = unwrap_after_approval
        self._unwrap_after_approval = after

    async def unwrap(self, secret_id: str) -> str:
        value = self._unwrap(secret_id)
        if isinstance(value, str):
            return value
        return await value

    async def unwrap_after_approval(self, secret_id: str, approval: ApprovalState | None) -> str:
        value = self._unwrap_after_approval(secret_id, approval)
        if isinstance(value, str):
            return value
        return await value


class K8sVaultSessionConnectionResolver:
    """把网关身份解析为 K8s exec 参数：集群表 + Vault 审批解包 + 可选 TokenRequest。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: K8sSecretUnwrapper,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        async with self._session_factory() as session:
            asset = await _load_asset(
                session, tenant_id=request.tenant_id, asset_id=request.asset_id
            )
            if asset is None or not asset.is_active or asset.asset_type != "cloud":
                raise K8sChannelError(
                    "K8S_TARGET_UNRESOLVED",
                    f"no k8s cluster for asset={request.asset_id}",
                )
            account = await _load_account(
                session,
                tenant_id=request.tenant_id,
                asset_id=asset.id,
                account_id=request.account_id,
                protocol=request.protocol,
            )
            if account is None or account.status != "active" or account.protocol != "k8s":
                raise K8sChannelError(
                    "K8S_TARGET_UNRESOLVED",
                    f"no k8s account for asset={request.asset_id} account={request.account_id}",
                )
            scope = ActorScope(
                user_id=request.subject_id,
                tenant_id=request.tenant_id,
                permissions=("admin",),
            )
            cluster = await get_cluster_by_asset(session, scope, asset.id)
            if cluster is None:
                raise K8sChannelError(
                    "K8S_CLUSTER_NOT_CONFIGURED",
                    f"asset={request.asset_id} has no k8s cluster config",
                )
            approval = await _approval_from_session(session, request, secret_id=account.secret_id)
            namespace_scope = resolve_namespace_scope(cluster, account)
            namespace = pick_namespace(namespace_scope)
            pod = account.k8s_default_pod.strip()
            if not pod:
                raise K8sChannelError(
                    "K8S_POD_NOT_CONFIGURED",
                    "account has no default pod for k8s exec",
                )

        try:
            if approval.status == "not_required":
                bootstrap_token = await self._secrets.unwrap(account.secret_id)
            else:
                bootstrap_token = await self._secrets.unwrap_after_approval(
                    account.secret_id, approval
                )
        except ValueError as exc:
            raise K8sChannelError("K8S_TOKEN_UNWRAP_DENIED", str(exc)) from exc
        except Exception as exc:
            raise K8sChannelError(
                "K8S_TARGET_UNRESOLVED",
                "cannot unwrap cluster token",
            ) from exc

        token = bootstrap_token
        if account.k8s_use_short_lived_token:
            try:
                issued = await request_service_account_token(
                    api_server=cluster.api_server,
                    server_ca_pem=cluster.server_ca_pem,
                    bootstrap_token=bootstrap_token,
                    namespace=namespace,
                    service_account=account.k8s_service_account,
                    expiration_seconds=account.k8s_token_ttl_seconds,
                )
                token = issued.token
            except K8sTokenRequestError as exc:
                raise K8sChannelError(exc.code, exc.detail) from exc

        return SessionConnectionSpec(
            mode=ConnectorSessionMode.K8S_EXEC,
            k8s=K8sConnectionBundle(
                target=K8sTarget(
                    api_server=cluster.api_server,
                    namespace=namespace,
                    pod=pod,
                    container=account.k8s_default_container,
                    server_ca=cluster.server_ca_pem,
                ),
                credential=K8sCredential(token=token),
                scope=NamespaceScope(namespaces=namespace_scope),
            ),
        )


async def _approval_from_session(
    session: AsyncSession,
    request: ConnectorDispatchRequest,
    *,
    secret_id: str,
) -> ApprovalState:
    result = await session.execute(
        select(SessionModel).where(SessionModel.id == request.session_id)
    )
    row = result.scalar_one_or_none()
    if row is None or not row.jit_grant_id:
        return ApprovalState(status="not_required")
    return ApprovalState(
        status="approved",
        grant_id=row.jit_grant_id,
        workflow_request_id=row.workflow_request_id,
        expires_at=datetime.now(UTC),
        constraints={"vault_secret_id": secret_id},
    )


async def _load_asset(session: AsyncSession, *, tenant_id: str, asset_id: str) -> Asset | None:
    try:
        numeric_id = int(asset_id)
    except ValueError:
        return None
    result = await session.execute(
        select(Asset).where(Asset.id == numeric_id).where(Asset.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _load_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    asset_id: int,
    account_id: str,
    protocol: str,
) -> Account | None:
    stmt = select(Account).where(Account.tenant_id == tenant_id).where(Account.asset_id == asset_id)
    if account_id.isdigit():
        result = await session.execute(stmt.where(Account.id == int(account_id)))
        return result.scalar_one_or_none()
    named = stmt.where(Account.username == account_id)
    if protocol:
        typed = await session.execute(named.where(Account.protocol == protocol))
        account = typed.scalar_one_or_none()
        if account is not None:
            return account
    result = await session.execute(named)
    return result.scalars().first()
