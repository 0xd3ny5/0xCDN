"""Unit tests for CacheService."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import pytest

from application.cache_service import CacheService
from application.metrics_service import InMemoryMetricsCollector
from config import CacheConfig
from domain.entities import CacheEntry, OriginResponse
from domain.ports import OriginClient
from infrastructure.cache.lru_store import LRUCacheStore
from infrastructure.circuit_breaker import CircuitBreaker
from tests.conftest import FakeOriginClient


def _make_entry(
    content: bytes,
    ttl: float = 3600.0,
    etag: Optional[str] = None,
    stale_while_revalidate: float = 0.0,
) -> CacheEntry:
    now = time.time()
    return CacheEntry(
        content=content,
        headers={"content-type": "application/octet-stream"},
        etag=etag,
        created_at=now,
        ttl=ttl,
        last_accessed=now,
        size=len(content),
        status_code=200,
        stale_while_revalidate=stale_while_revalidate,
    )


def _build_service(
    cache_store: LRUCacheStore,
    origin: FakeOriginClient,
    metrics: InMemoryMetricsCollector,
    config: CacheConfig,
    circuit_breaker: CircuitBreaker | None = None,
) -> CacheService:
    return CacheService(
        cache_store=cache_store,
        origin_client=origin,
        metrics=metrics,
        config=config,
        circuit_breaker=circuit_breaker,
    )


# ------------------------------------------------------------------
# Core cache tests
# ------------------------------------------------------------------


async def test_cache_hit(
    cache_store: LRUCacheStore,
    mock_origin_client: FakeOriginClient,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """When the cache already contains the entry, origin is not called."""
    from domain.value_objects import CacheKey

    key = CacheKey.from_request("GET", "/img/logo.png")
    entry = _make_entry(b"cached-logo")
    await cache_store.put(str(key), entry)

    service = _build_service(cache_store, mock_origin_client, mock_metrics, cache_config)
    content, status, headers, hit = await service.get_or_fetch("/img/logo.png")

    assert content == b"cached-logo"
    assert status == 200
    assert hit is True
    assert mock_origin_client.call_count == 0


async def test_cache_miss_fetches_origin(
    cache_store: LRUCacheStore,
    mock_origin_client: FakeOriginClient,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """On a cache miss the service fetches from origin and caches the result."""
    service = _build_service(cache_store, mock_origin_client, mock_metrics, cache_config)
    content, status, headers, hit = await service.get_or_fetch("/img/new.png")

    assert content == b"origin-content"
    assert status == 200
    assert hit is False
    assert mock_origin_client.call_count == 1

    # Subsequent request should be served from cache.
    content2, _, _, hit2 = await service.get_or_fetch("/img/new.png")
    assert content2 == b"origin-content"
    assert hit2 is True
    assert mock_origin_client.call_count == 1


async def test_request_coalescing(
    cache_store: LRUCacheStore,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """Concurrent requests for the same key coalesce."""
    gate = asyncio.Event()

    class SlowOriginClient(OriginClient):
        def __init__(self) -> None:
            self.call_count = 0

        async def fetch(
            self,
            path: str,
            headers: Optional[dict[str, str]] = None,
        ) -> OriginResponse:
            self.call_count += 1
            await gate.wait()
            return OriginResponse(
                content=b"coalesced",
                status_code=200,
                headers={"content-type": "text/plain"},
                etag=None,
            )

    slow_origin = SlowOriginClient()
    service = _build_service(cache_store, slow_origin, mock_metrics, cache_config)

    tasks = [
        asyncio.create_task(service.get_or_fetch("/coalesce"))
        for _ in range(3)
    ]

    await asyncio.sleep(0.05)
    gate.set()

    results = await asyncio.gather(*tasks)

    for content, status, _, _ in results:
        assert content == b"coalesced"
        assert status == 200

    assert slow_origin.call_count < 3


async def test_conditional_request_304(
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """When origin returns 304, the cached entry's TTL is refreshed."""
    from domain.value_objects import CacheKey

    cache_store = LRUCacheStore(max_size_bytes=10 * 1024)

    # Pre-populate cache with an entry that is already expired.
    key = CacheKey.from_request("GET", "/stale.txt")
    old_entry = CacheEntry(
        content=b"old-content",
        headers={"content-type": "text/plain"},
        etag='"etag-v1"',
        created_at=time.time() - 9999,
        ttl=1.0,
        last_accessed=time.time(),
        size=len(b"old-content"),
        status_code=200,
    )
    await cache_store.put(str(key), old_entry)

    # Origin will return 304 Not Modified.
    origin_304 = FakeOriginClient(
        content=b"",
        status_code=304,
        headers={},
        etag='"etag-v1"',
    )
    service = CacheService(
        cache_store=cache_store,
        origin_client=origin_304,
        metrics=mock_metrics,
        config=cache_config,
    )

    content, status, _, _ = await service.get_or_fetch("/stale.txt")

    # Content should be the old cached content, not empty.
    assert content == b"old-content"
    assert status == 200

    # Origin was called with If-None-Match header.
    assert origin_304.call_count == 1
    sent_headers = origin_304.received_headers[0]
    assert sent_headers is not None
    assert sent_headers.get("If-None-Match") == '"etag-v1"'


async def test_range_request(
    cache_store: LRUCacheStore,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """handle_range_request returns partial content with correct headers."""
    origin = FakeOriginClient(
        content=b"0123456789ABCDEF",
        status_code=200,
        headers={"content-type": "application/octet-stream"},
    )
    service = _build_service(cache_store, origin, mock_metrics, cache_config)

    partial, status, headers = await service.handle_range_request(
        "/data.bin", "bytes=0-4"
    )

    assert status == 206
    assert partial == b"01234"
    assert "Content-Range" in headers
    assert headers["Accept-Ranges"] == "bytes"


# ------------------------------------------------------------------
# Stale-While-Revalidate
# ------------------------------------------------------------------


async def test_stale_while_revalidate(
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """Expired entry within SWR window is served immediately; revalidation happens in background."""
    from domain.value_objects import CacheKey

    cache_store = LRUCacheStore(max_size_bytes=10 * 1024)

    # Create an expired entry with SWR window
    key = CacheKey.from_request("GET", "/swr.txt")
    entry = CacheEntry(
        content=b"stale-content",
        headers={"content-type": "text/plain"},
        etag='"v1"',
        created_at=time.time() - 10,  # 10 seconds ago
        ttl=5.0,                       # expired 5 seconds ago
        last_accessed=time.time(),
        size=len(b"stale-content"),
        status_code=200,
        stale_while_revalidate=60.0,   # but SWR allows up to 60s stale
    )
    await cache_store.put(str(key), entry)

    revalidation_gate = asyncio.Event()

    class SlowRevalidateOrigin(OriginClient):
        def __init__(self):
            self.call_count = 0

        async def fetch(self, path, headers=None):
            self.call_count += 1
            await revalidation_gate.wait()
            return OriginResponse(
                content=b"fresh-content",
                status_code=200,
                headers={"content-type": "text/plain"},
                etag='"v2"',
            )

    origin = SlowRevalidateOrigin()
    service = CacheService(
        cache_store=cache_store,
        origin_client=origin,
        metrics=mock_metrics,
        config=cache_config,
    )

    # Should return stale content immediately without waiting for origin
    content, status, _, hit = await service.get_or_fetch("/swr.txt")
    assert content == b"stale-content"
    assert hit is True  # served from cache (stale)

    # Background revalidation should be in progress
    assert len(service._revalidation_tasks) == 1

    # Let revalidation complete
    revalidation_gate.set()
    await asyncio.sleep(0.1)

    # Now cache should have fresh content
    content2, status2, _, hit2 = await service.get_or_fetch("/swr.txt")
    assert content2 == b"fresh-content"
    assert origin.call_count == 1

    await service.shutdown()


# ------------------------------------------------------------------
# Cache-Control parsing integration
# ------------------------------------------------------------------


async def test_cache_control_max_age(
    cache_store: LRUCacheStore,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """Origin Cache-Control: max-age overrides default TTL."""
    origin = FakeOriginClient(
        content=b"cc-content",
        status_code=200,
        headers={
            "content-type": "text/plain",
            "cache-control": "max-age=120",
        },
    )
    service = _build_service(cache_store, origin, mock_metrics, cache_config)
    await service.get_or_fetch("/cc.txt")

    # Verify the stored entry has TTL=120 not the default
    from domain.value_objects import CacheKey
    key = str(CacheKey.from_request("GET", "/cc.txt"))
    stored = await cache_store.get(key)
    assert stored is not None
    assert stored.ttl == 120.0


async def test_cache_control_no_store(
    cache_store: LRUCacheStore,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """Origin Cache-Control: no-store prevents caching."""
    origin = FakeOriginClient(
        content=b"private-content",
        status_code=200,
        headers={
            "content-type": "text/plain",
            "cache-control": "no-store",
        },
    )
    service = _build_service(cache_store, origin, mock_metrics, cache_config)
    content, status, _, _ = await service.get_or_fetch("/private.txt")

    assert content == b"private-content"
    # Should NOT be cached
    from domain.value_objects import CacheKey
    key = str(CacheKey.from_request("GET", "/private.txt"))
    stored = await cache_store.get(key)
    # Either not stored, or stored but the service should not have stored it
    # Since we return before put() when no-store, it should be None
    # But the LRU store now returns entries without checking expiry, so
    # let's check call count on second fetch
    content2, _, _, _ = await service.get_or_fetch("/private.txt")
    assert origin.call_count == 2  # origin called twice = not cached


async def test_cache_control_s_maxage(
    cache_store: LRUCacheStore,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """s-maxage takes priority over max-age."""
    origin = FakeOriginClient(
        content=b"s-content",
        status_code=200,
        headers={
            "content-type": "text/plain",
            "cache-control": "max-age=60, s-maxage=300",
        },
    )
    service = _build_service(cache_store, origin, mock_metrics, cache_config)
    await service.get_or_fetch("/s.txt")

    from domain.value_objects import CacheKey
    key = str(CacheKey.from_request("GET", "/s.txt"))
    stored = await cache_store.get(key)
    assert stored is not None
    assert stored.ttl == 300.0


# ------------------------------------------------------------------
# Circuit breaker
# ------------------------------------------------------------------


async def test_circuit_breaker_serves_stale_when_open(
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """When circuit is open, stale content is served instead of 503."""
    from domain.value_objects import CacheKey
    from infrastructure.circuit_breaker import CircuitBreaker, CircuitState

    cache_store = LRUCacheStore(max_size_bytes=10 * 1024)
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=300.0)

    # Pre-populate with expired entry
    key = CacheKey.from_request("GET", "/circuit.txt")
    entry = CacheEntry(
        content=b"stale-circuit",
        headers={"content-type": "text/plain"},
        etag=None,
        created_at=time.time() - 9999,
        ttl=1.0,
        last_accessed=time.time(),
        size=len(b"stale-circuit"),
        status_code=200,
    )
    await cache_store.put(str(key), entry)

    # Trip the circuit breaker
    await breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    # Dummy origin that should NOT be called
    origin = FakeOriginClient(content=b"should-not-see")
    service = CacheService(
        cache_store=cache_store,
        origin_client=origin,
        metrics=mock_metrics,
        config=cache_config,
        circuit_breaker=breaker,
    )

    content, status, _, _ = await service.get_or_fetch("/circuit.txt")
    assert content == b"stale-circuit"
    assert origin.call_count == 0  # origin was not called


async def test_circuit_breaker_503_when_no_stale(
    cache_store: LRUCacheStore,
    mock_metrics: InMemoryMetricsCollector,
    cache_config: CacheConfig,
) -> None:
    """When circuit is open and no stale content, return 503."""
    from infrastructure.circuit_breaker import CircuitBreaker, CircuitState

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=300.0)
    await breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    origin = FakeOriginClient(content=b"nope")
    service = CacheService(
        cache_store=cache_store,
        origin_client=origin,
        metrics=mock_metrics,
        config=cache_config,
        circuit_breaker=breaker,
    )

    content, status, _, _ = await service.get_or_fetch("/no-stale.txt")
    assert status == 503
    assert origin.call_count == 0
