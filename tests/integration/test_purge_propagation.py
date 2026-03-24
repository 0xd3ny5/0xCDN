"""Integration tests for cache purge operations.

Exercises the /internal/purge endpoint to verify exact-URL and
prefix-based purge against a real FastAPI app with in-memory cache.
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

class _SimpleOriginClient(OriginClient):
    """Origin that returns the path as content, so each file is distinct."""

    async def fetch(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> OriginResponse:
        return OriginResponse(
            content=path.encode(),
            status_code=200,
            headers={"content-type": "text/plain"},
            etag=None,
        )


def _build_app() -> tuple[FastAPI, LRUCacheStore]:
    config = CacheConfig(max_size_bytes=1024 * 1024, default_ttl=3600.0)
    cache_store = LRUCacheStore(max_size_bytes=config.max_size_bytes)
    metrics = InMemoryMetricsCollector()
    origin = _SimpleOriginClient()
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
        edge_id="purge-test-edge",
        cache_store=cache_store,
        auth_config=AuthConfig(secret_key="test", token_ttl=3600),
    )
    app.include_router(router)
    return app, cache_store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_purge_exact_url() -> None:
    """Purging an exact cache key removes only that entry."""
    app, cache_store = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Populate cache.
        await client.get("/files/img/a.png")
        await client.get("/files/img/b.png")

        keys_before = await cache_store.keys()
        assert len(keys_before) == 2

        # Determine the actual cache key format used by CacheService.
        key_a = [k for k in keys_before if "a.png" in k][0]

        # Purge one specific key.
        r = await client.delete("/internal/purge", params={"url": key_a})
        assert r.status_code == 200

        keys_after = await cache_store.keys()
        assert len(keys_after) == 1
        # The remaining key should be the one we did NOT purge.
        assert any("b.png" in k for k in keys_after)


async def test_purge_prefix() -> None:
    """Purging by prefix removes all matching entries."""
    app, cache_store = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Populate cache with files under two different prefixes.
        await client.get("/files/img/a.png")
        await client.get("/files/img/b.png")
        await client.get("/files/css/style.css")

        keys_before = await cache_store.keys()
        assert len(keys_before) == 3

        # Determine the prefix format from actual keys.
        img_keys = [k for k in keys_before if "img" in k]
        # Extract the common prefix (e.g. "GET:img/").
        prefix = img_keys[0].split("img/")[0] + "img/"

        # Purge all "img/" entries by prefix.
        r = await client.delete("/internal/purge", params={"prefix": prefix})
        assert r.status_code == 200
        body = r.json()
        assert body["purged_count"] == 2

        keys_after = await cache_store.keys()
        assert len(keys_after) == 1
        assert any("css" in k for k in keys_after)
