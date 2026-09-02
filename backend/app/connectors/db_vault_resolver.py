"""#t71 生产 Database SessionConnectionResolver：资产注册表 + Vault 审批解包。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sessions.service import ConnectorDispatchRequest
from app.connectors.db_channel_errors import DbChannelError
from app.connectors.mysql_proxy import MysqlCredential, MysqlTarget
from app.connectors.postgres_proxy import PostgresCredential, PostgresTarget
from app.connectors.session_runtime import ConnectorSessionMode, DbConnectionBundle, SessionConnectionSpec
from app.models.account import Account
from app.models.asset import Asset
from app.protocols.catalog import CRED_PASSWORD, PROTOCOL_BY_ID

DATABASE_PROTOCOLS = frozenset({"postgresql", "mysql", "mariadb"})
MYSQL_PROTOCOLS = frozenset({"mysql", "mariadb"})


class DbSecretUnwrapper(Protocol):
    async def unwrap(self, secret_id: str) -> str:
        """直接解包（仅用于无 JIT grant 的非生产/测试路径）。"""


class CallableDbSecretUnwrapper:
    def __init__(self, unwrap: Callable[[str], Awaitable[str] | str]) -> None:
        self._unwrap = unwrap

    async def unwrap(self, secret_id: str) -> str:
        value = self._unwrap(secret_id)
        if isinstance(value, str):
            return value
        return await value


class DatabaseVaultSessionConnectionResolver:
    """把网关身份解析为 PostgreSQL / MySQL COM_QUERY 参数。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        secrets: DbSecretUnwrapper,
        default_postgres_database: str = "postgres",
        default_mysql_database: str = "mysql",
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._default_postgres_database = default_postgres_database
        self._default_mysql_database = default_mysql_database

    async def resolve(self, request: ConnectorDispatchRequest) -> SessionConnectionSpec:
        protocol = request.protocol.lower()
        if protocol not in DATABASE_PROTOCOLS:
            raise DbChannelError(
                "DB_PROTOCOL_UNSUPPORTED",
                f"database resolver does not support protocol={protocol}",
            )

        async with self._session_factory() as session:
            asset = await _load_asset(session, tenant_id=request.tenant_id, asset_id=request.asset_id)
            if asset is None or not asset.is_active or asset.asset_type != "database":
                raise DbChannelError(
                    "DB_TARGET_UNRESOLVED",
                    f"no database asset for asset={request.asset_id}",
                )
            account = await _load_account(
                session,
                tenant_id=request.tenant_id,
                asset_id=asset.id,
                account_id=request.account_id,
                protocol=request.protocol,
            )
            if account is None or account.status != "active":
                raise DbChannelError(
                    "DB_TARGET_UNRESOLVED",
                    f"no database account for asset={request.asset_id} account={request.account_id}",
                )
            _validate_database_account(protocol=protocol, account=account)

        try:
            password = await self._secrets.unwrap(account.secret_id)
        except ValueError as exc:
            raise DbChannelError("DB_PASSWORD_UNWRAP_DENIED", str(exc)) from exc
        except Exception as exc:
            raise DbChannelError("DB_TARGET_UNRESOLVED", "cannot unwrap database password") from exc

        if protocol in MYSQL_PROTOCOLS:
            return SessionConnectionSpec(
                mode=ConnectorSessionMode.DB_MYSQL,
                db=DbConnectionBundle(
                    engine="mysql",
                    target=MysqlTarget(
                        host=asset.address,
                        port=asset.port,
                        database=self._default_mysql_database,
                        username=account.username,
                    ),
                    credential=MysqlCredential(password=password),
                ),
            )

        return SessionConnectionSpec(
            mode=ConnectorSessionMode.DB_POSTGRESQL,
            db=DbConnectionBundle(
                engine="postgresql",
                target=PostgresTarget(
                    host=asset.address,
                    port=asset.port,
                    database=self._default_postgres_database,
                    username=account.username,
                ),
                credential=PostgresCredential(password=password),
            ),
        )


def _validate_database_account(*, protocol: str, account: Account) -> None:
    definition = PROTOCOL_BY_ID.get(protocol)
    if definition is None or CRED_PASSWORD not in definition.credential_types:
        raise DbChannelError("DB_PROTOCOL_INVALID", f"invalid database protocol {protocol}")
    if not account.secret_id.strip():
        raise DbChannelError("DB_PASSWORD_SECRET_REQUIRED", "database account requires secret_id")
    if not account.username.strip():
        raise DbChannelError("DB_USERNAME_REQUIRED", "database account requires username")


async def _load_asset(
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
