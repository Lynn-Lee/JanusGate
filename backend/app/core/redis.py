"""Redis client construction for single-node, Sentinel, and Cluster deployments."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlparse

from redis.asyncio import Redis
from redis.asyncio.cluster import ClusterNode, RedisCluster
from redis.asyncio.sentinel import Sentinel

from app.core.config import Settings
from app.core.config import settings as app_settings

SentinelFactory = Callable[..., Any]
ClusterFactory = Callable[..., Any]


def parse_redis_node_urls(urls: str) -> list[tuple[str, int]]:
    nodes: list[tuple[str, int]] = []
    for raw_url in urls.split(","):
        url = raw_url.strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname or parsed.port is None:
            raise ValueError("Redis node URLs must include redis/rediss scheme, host, and port")
        nodes.append((parsed.hostname, parsed.port))
    if not nodes:
        raise ValueError("At least one Redis node URL is required")
    return nodes


def create_redis_client(
    *,
    settings: Settings = app_settings,
    sentinel_cls: SentinelFactory = Sentinel,
    cluster_cls: ClusterFactory = RedisCluster,
) -> Redis:
    if settings.REDIS_MODE == "single":
        return Redis.from_url(settings.REDIS_URL, decode_responses=True)
    if settings.REDIS_MODE == "sentinel":
        sentinel = sentinel_cls(
            parse_redis_node_urls(settings.REDIS_SENTINEL_URLS),
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            decode_responses=True,
        )
        return cast(
            Redis,
            sentinel.master_for(
                settings.REDIS_SENTINEL_MASTER_NAME,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
                decode_responses=True,
            ),
        )
    if settings.REDIS_MODE == "cluster":
        startup_nodes = [
            ClusterNode(host=host, port=port)
            for host, port in parse_redis_node_urls(settings.REDIS_CLUSTER_URLS)
        ]
        return cast(
            Redis,
            cluster_cls(
                startup_nodes=startup_nodes,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
                decode_responses=True,
            ),
        )
    raise ValueError("UNSUPPORTED_REDIS_MODE")
