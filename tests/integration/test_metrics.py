"""Integration tests for metrics collection through the full request path.

Verifies that the InMemoryMetricsCollector records accurate counts
after requests are served through the real FastAPI app.
"""

from __future__ import annotations

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

class _CountingOrigin(OriginClient):
    def __init__(self) -> None:
        self.call_count = 0

    async def fetch(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> OriginResponse:
        self.call_count += 1
        return OriginResponse(
            content=b"metrics-test-content",
            status_code=200,
            headers={"content-type": "text/plain"},
            etag=None,
        )


def _build_app() -> tuple[FastAPI, InMemoryMetricsCollector, _CountingOrigin]:
    config = CacheConfig(max_size_bytes=1024 * 1024, default_ttl=3600.0)
    cache_store = LRUCacheStore(max_size_bytes=config.max_size_bytes)
    metrics = InMemoryMetricsCollector()
    origin = _CountingOrigin()
    cache_service = CacheService(
        cache_store=cache_store,
        origin_client=origin,
        metrics=metrics,
        config=config,
    )

    app = FastAPI()
    router = create_router(
        cache_service=cache_service,
        metrics_collector=metrics,
        edge_id="metrics-edge",
        cache_store=cache_store,
        auth_config=AuthConfig(secret_key="test", token_ttl=3600),
    )
    app.include_router(router)
    return app, metrics, origin


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_metrics_after_requests() -> None:
    """After serving requests, metrics reflect accurate counts."""
    app, metrics, origin = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # First request -- cache miss, triggers origin fetch.
        await client.get("/files/metric-file.txt")
        # Second request -- cache hit.
        await client.get("/files/metric-file.txt")
        # Third request for a different file -- cache miss.
        await client.get("/files/another-file.txt")

    assert origin.call_count == 2

    edge_metrics = metrics.get_edge_metrics("metrics-edge")
    assert edge_metrics["total_requests"] == 3
    assert edge_metrics["bytes_served"] > 0

    aggregate = metrics.get_aggregate_metrics()
    assert aggregate["total_requests"] == 3

    # Origin fetch metrics (recorded by CacheService under edge_id "local").
    origin_metrics = metrics.get_edge_metrics("local")
    assert origin_metrics["bytes_fetched"] > 0
