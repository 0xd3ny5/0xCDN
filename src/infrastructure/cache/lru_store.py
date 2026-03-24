from __future__ import annotations

__all__: tuple[str, ...] = ("LRUCacheStore",)

import asyncio
import time
from collections import OrderedDict

from domain.ports import CacheEntry, CacheStore


class LRUCacheStore(CacheStore):
    """In-memory LRU cache with byte-level capacity and per-entry TTL."""

    def __init__(self, max_size_bytes: int = 100 * 1024 * 1024) -> None:
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size_bytes: int = max_size_bytes
        self._current_size_bytes: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

        # Counters
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    async def get(self, key: str) -> CacheEntry | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            # Don't evict expired entries here — the caller (CacheService)
            # decides whether to serve stale content via SWR.
            self._store.move_to_end(key)
            entry.last_accessed = time.time()
            self._hits += 1
            return entry

    async def put(self, key: str, entry: CacheEntry) -> None:
        async with self._lock:
            # If the key already exists, remove it first so size accounting
            # stays correct and the new entry lands at the MRU end.
            if key in self._store:
                self._remove_entry(key)

            needed = entry.size
            # Evict LRU entries until we have room (or the store is empty).
            while self._current_size_bytes + needed > self._max_size_bytes and self._store:
                self._evict_lru()

            self._store[key] = entry
            self._current_size_bytes += needed

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key not in self._store:
                return False
            self._remove_entry(key)
            return True

    async def delete_by_prefix(self, prefix: str) -> int:
        async with self._lock:
            keys_to_delete = [k for k in self._store if k.startswith(prefix)]
            for key in keys_to_delete:
                self._remove_entry(key)
            return len(keys_to_delete)

    async def keys(self) -> list[str]:
        async with self._lock:
            now = time.time()
            expired: list[str] = []
            valid: list[str] = []
            for key, entry in self._store.items():
                if self._is_expired(entry, now):
                    expired.append(key)
                else:
                    valid.append(key)
            for key in expired:
                self._remove_entry(key)
            return valid

    async def stats(self) -> dict:
        async with self._lock:
            total = self._hits + self._misses
            return {
                "total_entries": len(self._store),
                "total_size_bytes": self._current_size_bytes,
                "max_size_bytes": self._max_size_bytes,
                "hit_count": self._hits,
                "miss_count": self._misses,
                "eviction_count": self._evictions,
                "hit_ratio": self._hits / total if total > 0 else 0.0,
            }

    @staticmethod
    def _is_expired(entry: CacheEntry, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        return now - entry.created_at > entry.ttl

    def _remove_entry(self, key: str) -> None:
        entry = self._store.pop(key)
        self._current_size_bytes -= entry.size

    def _evict_lru(self) -> None:
        # OrderedDict iteration order is insertion/move-to-end order,
        # so the *first* item is the least recently used.
        key, entry = self._store.popitem(last=False)
        self._current_size_bytes -= entry.size
        self._evictions += 1
