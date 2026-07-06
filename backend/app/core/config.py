"""
JanusGate 应用配置 — pydantic-settings，类型安全，fail-closed 默认值。
"""
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: Literal["development", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # ── Database ──
    DATABASE_URL: str = "postgresql+asyncpg://janusgate:janusgate@localhost:5432/janusgate"
    DATABASE_READ_REPLICA_URL: str = ""
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MODE: Literal["single", "sentinel", "cluster"] = "single"
    REDIS_SENTINEL_URLS: str = ""
    REDIS_SENTINEL_MASTER_NAME: str = "mymaster"
    REDIS_CLUSTER_URLS: str = ""
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 5.0
    SESSION_CONNECTION_TOKEN_STORE: Literal["memory", "redis"] = "memory"
    SESSION_CONNECTION_TOKEN_REDIS_KEY_PREFIX: str = "janusgate:session:connection-token:"

    # ── Vault / KMS ──
    VAULT_LOCAL_KMS_MASTER_KEY: str = ""

    # ── Audit compliance report signing ──
    COMPLIANCE_REPORT_SIGNER_PROVIDER: Literal["local-hmac", "external-hmac"] = "local-hmac"
    COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID: str = ""
    COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET: str = ""

    # ── Edition / License ──
    JANUSGATE_EDITION: Literal["community", "enterprise"] = "community"
    JANUSGATE_LICENSE_KEY: str = ""
    JANUSGATE_LICENSE_SIGNING_SECRET: str = ""

    # ── Security ──
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── CORS ──
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── Rate Limiting ──
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 5
    RATE_LIMIT_GLOBAL_PER_MINUTE: int = 120

    # ── Assets ──
    ASSET_TEST_CONNECTION_ALLOWLIST: list[str] = []

    # ── Automation / Ansible ──
    ANSIBLE_PLAYBOOK_ROOT: str = "deploy/ansible/playbooks"
    ANSIBLE_RUNTIME_ROOT: str = "/var/lib/janusgate/ansible-runtime"
    ANSIBLE_PLAYBOOK_EXECUTABLE: str = "ansible-playbook"
    ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS: float = 300.0
    ANSIBLE_PLAYBOOK_MEMORY_LIMIT_MB: int = 0
    ANSIBLE_PLAYBOOK_CPU_LIMIT_SECONDS: int = 0

    @model_validator(mode="after")
    def enforce_secrets(self) -> "Settings":
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            raise ValueError(
                "SECRET_KEY 必须设置且长度不少于 32 字符。"
                "生成命令: python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if self.REDIS_MODE == "sentinel" and not self.REDIS_SENTINEL_URLS.strip():
            raise ValueError("REDIS_SENTINEL_URLS must be set when REDIS_MODE=sentinel")
        if self.REDIS_MODE == "sentinel" and not self.REDIS_SENTINEL_MASTER_NAME.strip():
            raise ValueError("REDIS_SENTINEL_MASTER_NAME must be set when REDIS_MODE=sentinel")
        if self.REDIS_MODE == "cluster" and not self.REDIS_CLUSTER_URLS.strip():
            raise ValueError("REDIS_CLUSTER_URLS must be set when REDIS_MODE=cluster")
        return self

    @property
    def celery_broker_url(self) -> str:
        return self.REDIS_URL

    @property
    def celery_result_backend(self) -> str:
        return self.REDIS_URL


settings = Settings()
