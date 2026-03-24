"""Integration tests for the full cache request flow.

Uses a real FastAPI app with an in-process fake origin client,
exercised via httpx.ASGITransport (no network calls).
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx
import pytest
from fastapi import FastAPI

from application.cache_service import CacheService
from application.metrics_service import InMemoryMetricsCollector
from config import AuthConfig, CacheConfig
from domain.entities import OriginResponse
from domain.ports import OriginClient
from infrastructure.cache.lru_store import LRUCacheStore
from presentation.edge.routes import create_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TrackingOriginClient(OriginClient):
    """Origin client that counts calls and returns configurable content."""

    def __init__(self, content: bytes = b"origin-payload") -> None:
        self.content = content
        self.call_count = 0

    async def fetch(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> OriginResponse:
        self.call_count += 1
        return OriginResponse(
            content=self.content,
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            etag='"test-etag"',
        )


def _build_app(
    cache_config: CacheConfig,
    origin: _TrackingOriginClient,
) -> tuple[FastAPI, LRUCacheStore, InMemoryMetricsCollector]:
    """Wire up a minimal edge FastAPI app for integration testing."""
    cache_store = LRUCacheStore(max_size_bytes=cache_config.max_size_bytes)
    metrics = InMemoryMetricsCollector()
    cache_service = CacheService(
        cache_store=cache_store,
        origin_client=origin,
        metrics=metrics,
        config=cache_config,
    )

    app = FastAPI()
    router = create_router(
        cache_service=cache_service,
        metrics_collector=metrics,
        edge_id="test-edge",
        cache_store=cache_store,
        auth_config=AuthConfig(secret_key="test-secret", token_ttl=3600),
    )
    app.include_router(router)
    return app, cache_store, metrics


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_full_cache_miss_then_hit() -> None:
    """First request is a cache miss (origin called), second is a cache hit."""
    origin = _TrackingOriginClient(content=b"hello-integration")
    config = CacheConfig(max_size_bytes=1024 * 1024, default_ttl=60.0)
    app, _, _ = _build_app(config, origin)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # First request -- cache miss
        r1 = await client.get("/files/test.txt")
        assert r1.status_code == 200
        assert r1.content == b"hello-integration"
        assert origin.call_count == 1

        # Second request -- cache hit (origin not called again)
        r2 = await client.get("/files/test.txt")
        assert r2.status_code == 200
        assert r2.content == b"hello-integration"
        assert origin.call_count == 1


async def test_ttl_expiry() -> None:
    """After TTL expires, the cache re-fetches from origin."""
    origin = _TrackingOriginClient(content=b"ttl-data")
    config = CacheConfig(max_size_bytes=1024 * 1024, default_ttl=0.2)
    app, _, _ = _build_app(config, origin)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/files/ttl.txt")
        assert r1.status_code == 200
        assert origin.call_count == 1

        # Wait for TTL to expire.
        await asyncio.sleep(0.3)

        r2 = await client.get("/files/ttl.txt")
        assert r2.status_code == 200
        # Origin should have been called again after expiry.
        assert origin.call_count == 2


async def test_eviction_under_pressure() -> None:
    """When the cache is full, LRU entries are evicted to make room."""
    origin = _TrackingOriginClient(content=b"A" * 512)
    # Only 1024 bytes capacity -- room for 2 x 512-byte entries.
    config = CacheConfig(max_size_bytes=1024, default_ttl=60.0)
    app, cache_store, _ = _build_app(config, origin)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Fill with two entries.
        await client.get("/files/one.bin")
        await client.get("/files/two.bin")
        assert origin.call_count == 2

        # Third entry should evict the LRU (one.bin).
        await client.get("/files/three.bin")
        assert origin.call_count == 3

        stats = await cache_store.stats()
        assert stats["eviction_count"] >= 1
        assert stats["total_entries"] == 2
