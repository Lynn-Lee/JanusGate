"""#t66 声明式协议与资产类型目录。

驱动模块字段仅作按需加载占位，核心镜像不内置数据库驱动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

ASSET_TYPE_HOST = "host"
ASSET_TYPE_DATABASE = "database"
ASSET_TYPE_DEVICE = "device"
ASSET_TYPE_WEB = "web"
ASSET_TYPE_CLOUD = "cloud"
ASSET_TYPE_CUSTOM = "custom"
ASSET_TYPE_DIRECTORY = "directory_service"
ASSET_TYPE_GPT = "gpt"

ALL_ASSET_TYPES: Final[tuple[str, ...]] = (
    ASSET_TYPE_HOST,
    ASSET_TYPE_DATABASE,
    ASSET_TYPE_DEVICE,
    ASSET_TYPE_WEB,
    ASSET_TYPE_CLOUD,
    ASSET_TYPE_CUSTOM,
    ASSET_TYPE_DIRECTORY,
    ASSET_TYPE_GPT,
)

CRED_PASSWORD = "password"
CRED_PRIVATE_KEY = "private_key"
CRED_TOKEN = "token"
CRED_CERTIFICATE = "certificate"


@dataclass(frozen=True)
class ProtocolDefinition:
    id: str
    name: str
    category: str
    default_port: int
    asset_types: tuple[str, ...]
    credential_types: tuple[str, ...]
    driver_module: str | None = None


PROTOCOL_CATALOG: Final[tuple[ProtocolDefinition, ...]] = (
    ProtocolDefinition("ssh", "SSH", "terminal", 22, (ASSET_TYPE_HOST, ASSET_TYPE_DEVICE, ASSET_TYPE_CLOUD), (CRED_PASSWORD, CRED_PRIVATE_KEY)),
    ProtocolDefinition("sftp", "SFTP", "terminal", 22, (ASSET_TYPE_HOST, ASSET_TYPE_DEVICE), (CRED_PASSWORD, CRED_PRIVATE_KEY)),
    ProtocolDefinition("telnet", "Telnet", "terminal", 23, (ASSET_TYPE_HOST, ASSET_TYPE_DEVICE), (CRED_PASSWORD,)),
    ProtocolDefinition("rdp", "RDP", "graphical", 3389, (ASSET_TYPE_HOST,), (CRED_PASSWORD,)),
    ProtocolDefinition("vnc", "VNC", "graphical", 5900, (ASSET_TYPE_HOST, ASSET_TYPE_DEVICE), (CRED_PASSWORD,)),
    ProtocolDefinition("winrm", "WinRM", "terminal", 5985, (ASSET_TYPE_HOST,), (CRED_PASSWORD, CRED_CERTIFICATE)),
    ProtocolDefinition("mysql", "MySQL", "database", 3306, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.mysql"),
    ProtocolDefinition("mariadb", "MariaDB", "database", 3306, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.mariadb"),
    ProtocolDefinition("postgresql", "PostgreSQL", "database", 5432, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.postgresql"),
    ProtocolDefinition("oracle", "Oracle", "database", 1521, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.oracle"),
    ProtocolDefinition("sqlserver", "SQL Server", "database", 1433, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.sqlserver"),
    ProtocolDefinition("db2", "DB2", "database", 50000, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.db2"),
    ProtocolDefinition("mongodb", "MongoDB", "database", 27017, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD, CRED_TOKEN), "janusgate.drivers.mongodb"),
    ProtocolDefinition("redis", "Redis", "database", 6379, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD, CRED_TOKEN), "janusgate.drivers.redis"),
    ProtocolDefinition("clickhouse", "ClickHouse", "database", 8123, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.clickhouse"),
    ProtocolDefinition("dameng", "达梦", "database", 5236, (ASSET_TYPE_DATABASE,), (CRED_PASSWORD,), "janusgate.drivers.dameng"),
    ProtocolDefinition("http", "HTTP", "web", 80, (ASSET_TYPE_WEB,), (CRED_PASSWORD, CRED_TOKEN)),
    ProtocolDefinition("https", "HTTPS", "web", 443, (ASSET_TYPE_WEB,), (CRED_PASSWORD, CRED_TOKEN)),
    ProtocolDefinition("k8s", "Kubernetes", "cloud", 443, (ASSET_TYPE_CLOUD,), (CRED_TOKEN,)),
    ProtocolDefinition("gpt", "GPT", "extension", 443, (ASSET_TYPE_GPT,), (CRED_TOKEN,)),
)

PROTOCOL_BY_ID: Final[dict[str, ProtocolDefinition]] = {item.id: item for item in PROTOCOL_CATALOG}


def protocols_for_asset_type(asset_type: str) -> tuple[ProtocolDefinition, ...]:
    return tuple(item for item in PROTOCOL_CATALOG if asset_type in item.asset_types)


def validate_protocol_for_asset(asset_type: str, protocol_id: str) -> bool:
    definition = PROTOCOL_BY_ID.get(protocol_id)
    if definition is None:
        return False
    return asset_type in definition.asset_types
