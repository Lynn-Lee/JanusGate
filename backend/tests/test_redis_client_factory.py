"""Redis client factory regression tests."""
from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.redis import create_redis_client, parse_redis_node_urls


def test_parse_redis_node_urls_requires_host_and_port() -> None:
    assert parse_redis_node_urls("redis://redis-a:26379/0, redis://redis-b:26380/0") == [
        ("redis-a", 26379),
        ("redis-b", 26380),
    ]


def test_create_redis_client_uses_sentinel_master() -> None:
    calls: dict[str, Any] = {}

    class FakeSentinel:
        def __init__(self, nodes: list[tuple[str, int]], **kwargs: Any) -> None:
            calls["nodes"] = nodes
            calls["kwargs"] = kwargs

        def master_for(self, master_name: str, **kwargs: Any) -> object:
            calls["master_name"] = master_name
            calls["master_kwargs"] = kwargs
            return "sentinel-client"

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        REDIS_MODE="sentinel",
        REDIS_SENTINEL_URLS="redis://redis-a:26379/0,redis://redis-b:26380/0",
        REDIS_SENTINEL_MASTER_NAME="janusgate-master",
        REDIS_SOCKET_TIMEOUT_SECONDS=3.5,
        _env_file=None,
    )

    client = create_redis_client(settings=settings, sentinel_cls=FakeSentinel)

    assert client == "sentinel-client"
    assert calls["nodes"] == [("redis-a", 26379), ("redis-b", 26380)]
    assert calls["kwargs"]["socket_timeout"] == 3.5
    assert calls["kwargs"]["decode_responses"] is True
    assert calls["master_name"] == "janusgate-master"
    assert calls["master_kwargs"]["decode_responses"] is True


def test_create_redis_client_uses_cluster_startup_nodes() -> None:
    calls: dict[str, Any] = {}

    class FakeCluster:
        def __init__(self, *, startup_nodes: list[Any], **kwargs: Any) -> None:
            calls["startup_nodes"] = startup_nodes
            calls["kwargs"] = kwargs

    settings = Settings(
        SECRET_KEY="test-secret-key-test-secret-key-32",
        REDIS_MODE="cluster",
        REDIS_CLUSTER_URLS="redis://redis-a:6379/0,redis://redis-b:6380/0",
        REDIS_SOCKET_TIMEOUT_SECONDS=4.0,
        _env_file=None,
    )

    client = create_redis_client(settings=settings, cluster_cls=FakeCluster)

    assert isinstance(client, FakeCluster)
    assert [(node.host, node.port) for node in calls["startup_nodes"]] == [
        ("redis-a", 6379),
        ("redis-b", 6380),
    ]
    assert calls["kwargs"]["socket_timeout"] == 4.0
    assert calls["kwargs"]["decode_responses"] is True
