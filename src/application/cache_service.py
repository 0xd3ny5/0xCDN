"""Core cache orchestration service.

Handles cache lookups, origin fetches with request coalescing (dog-pile
prevention), conditional requests, HTTP range requests, stale-while-revalidate,
Cache-Control header parsing, and Vary header support.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from config import CacheConfig
from domain.entities import CacheEntry, OriginResponse
from domain.ports import CacheStore, MetricsCollector, OriginClient
from domain.value_objects import ByteRange, CacheKey
from infrastructure.cache_control import CacheDirectives, parse_cache_control
from infrastructure.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger("src.cache_service")


class CacheService:
    """Orchestrates cache reads, origin fetches, and range requests."""

    def __init__(
        self,
        cache_store: CacheStore,
        origin_client: OriginClient,
        metrics: MetricsCollector,
        config: CacheConfig,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._cache_store = cache_store
        self._origin_client = origin_client
        self._metrics = metrics
        self._config = config
        self._circuit_breaker = circuit_breaker or CircuitBreaker()

        # Dog-pile / request-coalescing state
        self._inflight: dict[str, asyncio.Event] = {}

        # Background revalidation tasks (SWR)
        self._revalidation_tasks: set[asyncio.Task] = set()

    async def get_or_fetch(
        self,
        path: str,
        request_headers: Optional[dict[str, str]] = None,
    ) -> tuple[bytes, int, dict[str, str], bool]:
        """Return cached content or fetch from origin.

        Args:
            path: The URL path to retrieve.
            request_headers: Optional headers from the downstream client.

        Returns:
            A tuple of ``(content, status_code, response_headers, cache_hit)``.
        """
        vary_headers = self._extract_vary_headers(request_headers)
        cache_key = CacheKey.from_request("GET", path, vary_headers=vary_headers)
        key_str = str(cache_key)

        # cache lookup
        cached = await self._cache_store.get(key_str)
        if cached is not None:
            if not cached.is_expired():
                # Fresh hit
                cached.touch()
                return cached.content, cached.status_code, dict(cached.headers), True

            if cached.is_stale_servable():
                # Stale-While-Revalidate: serve stale, revalidate in background
                self._schedule_background_revalidation(key_str, path, cached, request_headers)
                cached.touch()
                return cached.content, cached.status_code, dict(cached.headers), True

        # circuit breaker: if open, try to serve stale or return 503
        try:
            self._circuit_breaker.state  # just check
        except Exception:
            pass

        if self._circuit_breaker.state.value == "open":
            # Circuit is open - serve stale if available, else 503
            if cached is not None:
                logger.warning("Circuit open, serving stale content for %s", path)
                return cached.content, cached.status_code, dict(cached.headers), True
            logger.warning("Circuit open, no stale content for %s", path)
            return b"Service Unavailable: origin is down", 503, {"Content-Type": "text/plain"}, False

        # request coalescing
        if key_str in self._inflight:
            await self._inflight[key_str].wait()
            cached_after = await self._cache_store.get(key_str)
            if cached_after is not None:
                return cached_after.content, cached_after.status_code, dict(cached_after.headers), False
            return await self._do_fetch_and_cache(key_str, path, cached, request_headers)

        # initiate origin fetch (leader path)
        event = asyncio.Event()
        self._inflight[key_str] = event
        try:
            result = await self._do_fetch_and_cache(key_str, path, cached, request_headers)
            return result
        except CircuitOpenError:
            if cached is not None:
                return cached.content, cached.status_code, dict(cached.headers), True
            return b"Service Unavailable: origin is down", 503, {"Content-Type": "text/plain"}, False
        finally:
            event.set()
            self._inflight.pop(key_str, None)

    async def handle_range_request(
        self,
        path: str,
        range_header: str,
        request_headers: Optional[dict[str, str]] = None,
    ) -> tuple[bytes, int, dict[str, str]]:
        """Serve a partial-content (HTTP 206) range request."""
        ranges = ByteRange.from_header(range_header)
        byte_range = ranges[0]

        full_content, status_code, headers, _ = await self.get_or_fetch(path, request_headers)
        total = len(full_content)

        if byte_range.start is None and byte_range.end is not None:
            start = max(total - byte_range.end, 0)
            end = total - 1
        elif byte_range.end is None and byte_range.start is not None:
            start = byte_range.start
            end = total - 1
        else:
            start = byte_range.start if byte_range.start is not None else 0
            end = byte_range.end if byte_range.end is not None else total - 1

        end = min(end, total - 1)
        partial = full_content[start : end + 1]

        resp_headers = dict(headers)
        resp_headers["Content-Range"] = byte_range.content_range(total)
        resp_headers["Content-Length"] = str(len(partial))
        resp_headers["Accept-Ranges"] = "bytes"

        return partial, 206, resp_headers

    async def shutdown(self) -> None:
        """Cancel all background revalidation tasks."""
        for task in self._revalidation_tasks:
            task.cancel()
        if self._revalidation_tasks:
            await asyncio.gather(*self._revalidation_tasks, return_exceptions=True)
        self._revalidation_tasks.clear()

    def _extract_vary_headers(
        self, request_headers: Optional[dict[str, str]]
    ) -> Optional[dict[str, str]]:
        """Extract Vary-relevant headers from the request.

        For now, we always include accept-encoding in the vary key since
        our edge compresses responses. In a real CDN, this would be driven
        by the Vary header from the origin's previous response.
        """
        if not request_headers:
            return None
        # Normalize header names to lowercase
        normalized = {k.lower(): v for k, v in request_headers.items()}
        vary_values: dict[str, str] = {}
        if "accept-encoding" in normalized:
            vary_values["accept-encoding"] = normalized["accept-encoding"]
        return vary_values if vary_values else None

    def _schedule_background_revalidation(
        self,
        key_str: str,
        path: str,
        cached: CacheEntry,
        request_headers: Optional[dict[str, str]],
    ) -> None:
        """Kick off a background task to revalidate a stale entry."""
        if key_str in self._inflight:
            return  # already revalidating

        async def _revalidate() -> None:
            try:
                await self._do_fetch_and_cache(key_str, path, cached, request_headers)
            except Exception as exc:
                logger.debug("Background revalidation failed for %s: %s", path, exc)
            finally:
                self._revalidation_tasks.discard(task)

        task = asyncio.create_task(_revalidate())
        self._revalidation_tasks.add(task)

    async def _do_fetch_and_cache(
        self,
        key_str: str,
        path: str,
        cached: Optional[CacheEntry],
        request_headers: Optional[dict[str, str]] = None,
    ) -> tuple[bytes, int, dict[str, str], bool]:
        """Fetch from origin, update cache, and return the result."""
        fetch_headers: dict[str, str] = {}
        if cached is not None and cached.etag:
            fetch_headers["If-None-Match"] = cached.etag

        # Use circuit breaker for origin fetch
        async with self._circuit_breaker:
            origin_resp = await self._fetch_from_origin(path, fetch_headers)

        # Parse Cache-Control from origin response
        cc_header = origin_resp.headers.get("cache-control", "")
        directives = parse_cache_control(cc_header) if cc_header else CacheDirectives()

        # Parse Vary from origin response
        vary_header = origin_resp.headers.get("vary", "")
        vary_fields = [v.strip().lower() for v in vary_header.split(",") if v.strip()] if vary_header else None

        # If origin says no-store or private, don't cache at all
        if not directives.is_cacheable:
            return origin_resp.content, origin_resp.status_code, dict(origin_resp.headers), False

        # Compute effective TTL from Cache-Control
        effective_ttl = directives.effective_ttl(self._config.default_ttl)
        swr_window = directives.effective_stale_while_revalidate()

        if origin_resp.status_code == 304 and cached is not None:
            # Not Modified — refresh TTL on existing entry
            refreshed = CacheEntry(
                content=cached.content,
                headers=cached.headers,
                etag=cached.etag,
                created_at=time.time(),
                ttl=effective_ttl,
                last_accessed=time.time(),
                size=cached.size,
                status_code=cached.status_code,
                stale_while_revalidate=swr_window,
                vary_headers=vary_fields or cached.vary_headers,
            )
            await self._cache_store.put(key_str, refreshed)
            return refreshed.content, refreshed.status_code, dict(refreshed.headers), False

        # 200 (or any other cacheable status) — create new entry
        now = time.time()

        # If origin sent Vary, rebuild the cache key with vary header values
        if vary_fields and request_headers:
            normalized_req = {k.lower(): v for k, v in request_headers.items()}
            vary_values = {f: normalized_req.get(f, "") for f in vary_fields}
            new_key = CacheKey.from_request("GET", path, vary_headers=vary_values)
            key_str = str(new_key)

        entry = CacheEntry(
            content=origin_resp.content,
            headers=dict(origin_resp.headers),
            etag=origin_resp.etag,
            created_at=now,
            ttl=effective_ttl,
            last_accessed=now,
            size=len(origin_resp.content),
            status_code=origin_resp.status_code,
            stale_while_revalidate=swr_window,
            vary_headers=vary_fields,
        )
        await self._cache_store.put(key_str, entry)

        return origin_resp.content, origin_resp.status_code, dict(origin_resp.headers), False

    async def _fetch_from_origin(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> OriginResponse:
        """Wrap ``origin_client.fetch`` with timing instrumentation."""
        start = time.monotonic()
        response = await self._origin_client.fetch(path, headers)
        elapsed = time.monotonic() - start

        self._metrics.record_origin_fetch(
            edge_id="local",
            path=path,
            fetch_time=elapsed,
            bytes_fetched=len(response.content),
        )
        return response
