"""Tiered (hot/cold) cache store with in-memory LRU and disk-based tiers."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional, Callable

from domain.entities import CacheEntry
from domain.ports import CacheStore
from infrastructure.cache.lru_store import LRUCacheStore


def _url_safe_key(key: str) -> str:
    """Encode a cache key into a filesystem-safe directory name.

    Replaces characters that are problematic on common filesystems with
    underscores, then collapses consecutive underscores.
    """
    safe = re.sub(r'[/:?#\[\]@!$&\'()*+,;=%\\<>"|^`{} ]', "_", key)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "_empty_"


class _EvictingLRUCacheStore(LRUCacheStore):
    """LRU store that calls a callback instead of silently discarding evicted entries."""

    def __init__(
        self,
        max_size_bytes: int,
        on_evict: Callable[[str, CacheEntry], None],
    ) -> None:
        super().__init__(max_size_bytes=max_size_bytes)
        self._on_evict = on_evict

    # Override the internal evict helper so demoted entries go to cold tier.
    def _evict_lru(self) -> None:
        key, entry = self._store.popitem(last=False)
        self._current_size_bytes -= entry.size
        self._evictions += 1
        self._on_evict(key, entry)


class TieredCacheStore(CacheStore):
    """Hot/cold tiered cache store.

    * **Hot tier** -- a small, fast in-memory LRU (``LRUCacheStore``).
    * **Cold tier** -- a larger, slower disk-based store.

    When an entry is evicted from the hot tier it is automatically
    *demoted* to the cold tier rather than being discarded.  A cold-tier
    hit triggers *promotion* back into the hot tier.
    """

    def __init__(
        self,
        hot_max_size_bytes: int = 10 * 1024 * 1024,
        cold_dir: str = "/tmp/cdn_cold_cache",
        default_ttl: float = 3600.0,
    ) -> None:
        self._cold_dir = Path(cold_dir)
        self._cold_dir.mkdir(parents=True, exist_ok=True)
        self._default_ttl = default_ttl

        # Pending demotions queued by the synchronous eviction callback.
        self._pending_demotions: list[tuple[str, CacheEntry]] = []

        self._hot = _EvictingLRUCacheStore(
            max_size_bytes=hot_max_size_bytes,
            on_evict=self._queue_demotion,
        )
        self._cold_lock = asyncio.Lock()

        # Tier-level metrics
        self._hot_hits: int = 0
        self._hot_misses: int = 0
        self._cold_hits: int = 0
        self._cold_misses: int = 0
        self._promotions: int = 0
        self._demotions: int = 0

    async def get(self, key: str) -> Optional[CacheEntry]:
        entry = await self._hot.get(key)
        if entry is not None:
            self._hot_hits += 1
            return entry

        self._hot_misses += 1

        # Check cold tier.
        async with self._cold_lock:
            entry = self._cold_read(key)

        if entry is None:
            self._cold_misses += 1
            return None

        if entry.is_expired():
            async with self._cold_lock:
                self._cold_delete(key)
            self._cold_misses += 1
            return None

        # Promote to hot (remove from cold first).
        self._cold_hits += 1
        self._promotions += 1
        async with self._cold_lock:
            self._cold_delete(key)

        entry.touch()
        await self._hot.put(key, entry)
        # Flush any demotions triggered by the hot-tier put.
        await self._flush_demotions()
        return entry

    async def put(self, key: str, entry: CacheEntry) -> None:
        # Remove from cold if present so we don't have stale duplicates.
        async with self._cold_lock:
            self._cold_delete(key)

        await self._hot.put(key, entry)
        # Flush any demotions triggered by the hot-tier put.
        await self._flush_demotions()

    async def delete(self, key: str) -> bool:
        hot_removed = await self._hot.delete(key)
        async with self._cold_lock:
            cold_removed = self._cold_delete(key)
        return hot_removed or cold_removed

    async def delete_by_prefix(self, prefix: str) -> int:
        count = await self._hot.delete_by_prefix(prefix)
        async with self._cold_lock:
            count += self._cold_delete_by_prefix(prefix)
        return count

    async def keys(self) -> list[str]:
        hot_keys = set(await self._hot.keys())
        async with self._cold_lock:
            cold_keys = self._cold_keys()
        return list(hot_keys | cold_keys)

    async def stats(self) -> dict:
        hot_stats = await self._hot.stats()
        async with self._cold_lock:
            cold_entry_count, cold_size = self._cold_stats()

        total_hits = self._hot_hits + self._cold_hits
        total_misses = self._hot_misses + self._cold_misses
        total = total_hits + total_misses

        return {
            "hot": hot_stats,
            "cold": {
                "total_entries": cold_entry_count,
                "total_size_bytes": cold_size,
            },
            "hot_hits": self._hot_hits,
            "hot_misses": self._hot_misses,
            "cold_hits": self._cold_hits,
            "cold_misses": self._cold_misses,
            "promotions": self._promotions,
            "demotions": self._demotions,
            "total_hit_count": total_hits,
            "total_miss_count": total_misses,
            "hit_ratio": total_hits / total if total > 0 else 0.0,
        }

    def _queue_demotion(self, key: str, entry: CacheEntry) -> None:
        """Synchronous callback invoked inside _EvictingLRUCacheStore._evict_lru.

        We cannot perform async disk I/O here, so we buffer the evicted
        entry and flush it to cold storage after the hot-tier operation
        completes.
        """
        self._pending_demotions.append((key, entry))

    async def _flush_demotions(self) -> None:
        """Write all pending demotions to the cold tier."""
        if not self._pending_demotions:
            return
        demotions = self._pending_demotions[:]
        self._pending_demotions.clear()
        async with self._cold_lock:
            for key, entry in demotions:
                self._cold_write(key, entry)
                self._demotions += 1

    def _key_dir(self, key: str) -> Path:
        return self._cold_dir / _url_safe_key(key)

    def _cold_write(self, key: str, entry: CacheEntry) -> None:
        """Serialize a CacheEntry to disk as JSON metadata + raw bytes."""
        entry_dir = self._key_dir(key)
        entry_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "key": key,
            "headers": entry.headers,
            "etag": entry.etag,
            "created_at": entry.created_at,
            "ttl": entry.ttl,
            "last_accessed": entry.last_accessed,
            "size": entry.size,
            "status_code": entry.status_code,
        }
        (entry_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (entry_dir / "data.bin").write_bytes(entry.content)

    def _cold_read(self, key: str) -> Optional[CacheEntry]:
        """Deserialize a CacheEntry from disk, or return None."""
        entry_dir = self._key_dir(key)
        meta_path = entry_dir / "meta.json"
        data_path = entry_dir / "data.bin"

        if not meta_path.exists() or not data_path.exists():
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            content = data_path.read_bytes()
        except (json.JSONDecodeError, OSError):
            return None

        return CacheEntry(
            content=content,
            headers=meta["headers"],
            etag=meta.get("etag"),
            created_at=meta["created_at"],
            ttl=meta["ttl"],
            last_accessed=meta["last_accessed"],
            size=meta["size"],
            status_code=meta["status_code"],
        )

    def _cold_delete(self, key: str) -> bool:
        """Remove a single entry from the cold tier.  Returns True if removed."""
        entry_dir = self._key_dir(key)
        if not entry_dir.exists():
            return False
        # Remove files then directory.
        for child in entry_dir.iterdir():
            child.unlink()
        entry_dir.rmdir()
        return True

    def _cold_delete_by_prefix(self, prefix: str) -> int:
        """Remove all cold entries whose original key starts with *prefix*."""
        count = 0
        if not self._cold_dir.exists():
            return count
        for entry_dir in list(self._cold_dir.iterdir()):
            meta_path = entry_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if meta.get("key", "").startswith(prefix):
                for child in entry_dir.iterdir():
                    child.unlink()
                entry_dir.rmdir()
                count += 1
        return count

    def _cold_keys(self) -> set[str]:
        """Return the set of original keys stored in the cold tier."""
        result: set[str] = set()
        if not self._cold_dir.exists():
            return result
        now = time.time()
        expired_dirs: list[Path] = []
        for entry_dir in self._cold_dir.iterdir():
            meta_path = entry_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            key = meta.get("key", "")
            # Prune expired entries while scanning.
            if now - meta["created_at"] > meta["ttl"]:
                expired_dirs.append(entry_dir)
            else:
                result.add(key)
        for entry_dir in expired_dirs:
            for child in entry_dir.iterdir():
                child.unlink()
            entry_dir.rmdir()
        return result

    def _cold_stats(self) -> tuple[int, int]:
        """Return (entry_count, total_bytes) for the cold tier."""
        count = 0
        total_bytes = 0
        if not self._cold_dir.exists():
            return count, total_bytes
        for entry_dir in self._cold_dir.iterdir():
            data_path = entry_dir / "data.bin"
            if data_path.exists():
                count += 1
                total_bytes += data_path.stat().st_size
        return count, total_bytes
