"""Shared test fixtures for the CDN test suite."""

from __future__ import annotations

import time
from typing import Optional

import pytest

from application.metrics_service import InMemoryMetricsCollector
from config import CacheConfig, EdgeConfig
from domain.entities import CacheEntry, OriginResponse
from domain.ports import OriginClient
from infrastructure.cache.lru_store import LRUCacheStore


# ---------------------------------------------------------------------------
# Cache store
# ---------------------------------------------------------------------------

@pytest.fixture
def cache_store() -> LRUCacheStore:
    """LRU cache store with a small 10 KB capacity for testing."""
    return LRUCacheStore(max_size_bytes=10 * 1024)


# ---------------------------------------------------------------------------
# Sample cache entry
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_entry() -> CacheEntry:
    """A pre-built CacheEntry with deterministic test content."""
    content = b"Hello, CDN world!"
    now = time.time()
    return CacheEntry(
        content=content,
        headers={"content-type": "text/plain"},
        etag='"abc123"',
        created_at=now,
        ttl=3600.0,
        last_accessed=now,
        size=len(content),
        status_code=200,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@pytest.fixture
def cache_config() -> CacheConfig:
    """CacheConfig with small sizes suitable for testing."""
    return CacheConfig(
        max_size_bytes=10 * 1024,
        default_ttl=60.0,
        hot_tier_max_items=100,
        cold_tier_path="/tmp/cdn_test_cold",
    )


@pytest.fixture
def edge_config() -> EdgeConfig:
    """EdgeConfig with test values."""
    return EdgeConfig(
        edge_id="test-edge-1",
        host="127.0.0.1",
        port=8080,
        region="us-east-1",
        origin_url="http://localhost:9000",
        shield_url=None,
    )


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeOriginClient(OriginClient):
    """Simple origin client that returns configurable responses.

    Tracks the number of fetch calls and the paths requested.
    """

    def __init__(
        self,
        content: bytes = b"origin-content",
        status_code: int = 200,
        headers: Optional[dict[str, str]] = None,
        etag: Optional[str] = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/octet-stream"}
        self.etag = etag
        self.call_count = 0
        self.requested_paths: list[str] = []
        self.received_headers: list[Optional[dict[str, str]]] = []

    async def fetch(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> OriginResponse:
        self.call_count += 1
        self.requested_paths.append(path)
        self.received_headers.append(headers)
        return OriginResponse(
            content=self.content,
            status_code=self.status_code,
            headers=dict(self.headers),
            etag=self.etag,
        )


@pytest.fixture
def mock_origin_client() -> FakeOriginClient:
    """A FakeOriginClient that returns default test content."""
    return FakeOriginClient(
        content=b"origin-content",
        status_code=200,
        headers={"content-type": "application/octet-stream"},
        etag='"origin-etag"',
    )


@pytest.fixture
def mock_metrics() -> InMemoryMetricsCollector:
    """An InMemoryMetricsCollector instance for test use."""
    return InMemoryMetricsCollector()
