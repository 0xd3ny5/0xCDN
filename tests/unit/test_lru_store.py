"""Unit tests for LRUCacheStore."""

from __future__ import annotations

import asyncio
import time

import pytest

from domain.entities import CacheEntry
from infrastructure.cache.lru_store import LRUCacheStore


def _make_entry(content: bytes, ttl: float = 3600.0) -> CacheEntry:
    """Helper to create a CacheEntry with minimal boilerplate."""
    now = time.time()
    return CacheEntry(
        content=content,
        headers={"content-type": "text/plain"},
        etag=None,
        created_at=now,
        ttl=ttl,
        last_accessed=now,
        size=len(content),
        status_code=200,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_put_and_get(cache_store: LRUCacheStore, sample_entry: CacheEntry) -> None:
    """Storing an entry and retrieving it returns the same content."""
    await cache_store.put("key1", sample_entry)
    result = await cache_store.get("key1")

    assert result is not None
    assert result.content == sample_entry.content
    assert result.status_code == sample_entry.status_code


async def test_get_expired_entry(cache_store: LRUCacheStore) -> None:
    """An expired entry is still returned (for SWR decisions) but is_expired() is True."""
    entry = _make_entry(b"ephemeral", ttl=0.1)
    await cache_store.put("expire-me", entry)

    await asyncio.sleep(0.15)

    result = await cache_store.get("expire-me")
    assert result is not None
    assert result.is_expired() is True
    assert result.content == b"ephemeral"


async def test_lru_eviction() -> None:
    """When the cache is full, the least-recently-used entry is evicted."""
    # 100-byte capacity; each entry is 40 bytes -> room for 2 entries.
    store = LRUCacheStore(max_size_bytes=100)

    entry_a = _make_entry(b"A" * 40)
    entry_b = _make_entry(b"B" * 40)
    entry_c = _make_entry(b"C" * 40)

    await store.put("a", entry_a)
    await store.put("b", entry_b)
    # Adding 'c' must evict 'a' (LRU).
    await store.put("c", entry_c)

    assert await store.get("a") is None
    assert (await store.get("b")) is not None
    assert (await store.get("c")) is not None

    stats = await store.stats()
    assert stats["eviction_count"] >= 1


async def test_delete(cache_store: LRUCacheStore, sample_entry: CacheEntry) -> None:
    """Deleting a key removes it from the store."""
    await cache_store.put("del-key", sample_entry)
    deleted = await cache_store.delete("del-key")
    assert deleted is True

    result = await cache_store.get("del-key")
    assert result is None

    # Deleting a non-existent key returns False.
    assert await cache_store.delete("no-such-key") is False


async def test_delete_by_prefix(cache_store: LRUCacheStore) -> None:
    """delete_by_prefix removes all keys matching the given prefix."""
    await cache_store.put("img/a.png", _make_entry(b"a"))
    await cache_store.put("img/b.png", _make_entry(b"b"))
    await cache_store.put("css/style.css", _make_entry(b"c"))

    count = await cache_store.delete_by_prefix("img/")
    assert count == 2

    assert await cache_store.get("img/a.png") is None
    assert await cache_store.get("img/b.png") is None
    assert (await cache_store.get("css/style.css")) is not None


async def test_keys_excludes_expired(cache_store: LRUCacheStore) -> None:
    """keys() returns only non-expired entries and cleans up expired ones."""
    await cache_store.put("fresh", _make_entry(b"f", ttl=3600.0))
    await cache_store.put("stale", _make_entry(b"s", ttl=0.1))

    await asyncio.sleep(0.15)

    keys = await cache_store.keys()
    assert "fresh" in keys
    assert "stale" not in keys


async def test_stats_tracking(cache_store: LRUCacheStore) -> None:
    """Hit, miss, and eviction counters are tracked correctly."""
    entry = _make_entry(b"data")

    await cache_store.put("k", entry)
    await cache_store.get("k")       # hit
    await cache_store.get("k")       # hit
    await cache_store.get("missing") # miss

    stats = await cache_store.stats()
    assert stats["hit_count"] == 2
    assert stats["miss_count"] == 1
    assert stats["total_entries"] == 1


async def test_put_updates_existing(cache_store: LRUCacheStore) -> None:
    """Overwriting an existing key updates the content and size accounting."""
    entry_v1 = _make_entry(b"version1")
    entry_v2 = _make_entry(b"version2-longer")

    await cache_store.put("key", entry_v1)
    await cache_store.put("key", entry_v2)

    result = await cache_store.get("key")
    assert result is not None
    assert result.content == b"version2-longer"

    stats = await cache_store.stats()
    assert stats["total_entries"] == 1
    assert stats["total_size_bytes"] == len(b"version2-longer")


async def test_concurrent_access(cache_store: LRUCacheStore) -> None:
    """Parallel gets and puts do not raise exceptions or corrupt state."""

    async def writer(i: int) -> None:
        entry = _make_entry(f"data-{i}".encode())
        await cache_store.put(f"concurrent-{i}", entry)

    async def reader(i: int) -> None:
        await cache_store.get(f"concurrent-{i}")

    tasks = []
    for i in range(20):
        tasks.append(asyncio.create_task(writer(i)))
        tasks.append(asyncio.create_task(reader(i)))

    await asyncio.gather(*tasks)

    # Verify the store is in a consistent state.
    stats = await cache_store.stats()
    assert stats["total_entries"] <= 20
    assert stats["total_size_bytes"] >= 0
