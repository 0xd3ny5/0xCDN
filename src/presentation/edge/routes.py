"""Edge node routes.

Serves cached content to end-users, proxying to the origin on cache
misses.  Supports signed-URL authentication, range requests,
compression, and internal purge/stats endpoints.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response

from application.cache_service import CacheService
from config import AuthConfig
from domain.ports import CacheStore, MetricsCollector
from infrastructure.auth import validate_token
from infrastructure.compression import compress_response


def create_router(
    cache_service: CacheService,
    metrics_collector: MetricsCollector,
    edge_id: str,
    cache_store: CacheStore,
    auth_config: AuthConfig | None = None,
) -> APIRouter:
    """Create the edge node API router.

    Args:
        cache_service: Service that handles cache lookups and origin fetches.
        metrics_collector: Collector for recording request metrics.
        edge_id: Unique identifier for this edge node.
        cache_store: The underlying cache store (used for purge/stats).
        auth_config: Optional authentication configuration for signed URLs.

    Returns:
        A configured ``APIRouter`` with file serving, health, purge, and
        stats endpoints.
    """
    router = APIRouter()

    @router.get("/files/{path:path}")
    async def serve_file(
        path: str,
        request: Request,
        token: Optional[str] = Query(None),
        expires: Optional[int] = Query(None),
        range_header: Optional[str] = Header(None, alias="range"),
        accept_encoding: Optional[str] = Header(None, alias="accept-encoding"),
        x_cdn_token: Optional[str] = Header(None, alias="x-cdn-token"),
    ) -> Response:
        """Serve a file through the edge cache.

        Validates signed URLs when a token is present, delegates to the
        cache service for content retrieval, and applies compression
        based on Accept-Encoding.
        """
        start_time = time.monotonic()

        # Signed URL validation
        provided_token = x_cdn_token or token
        if provided_token and auth_config:
            exp = expires or 0
            if not validate_token(
                path=f"/files/{path}",
                token=provided_token,
                expires_at=exp,
                secret_key=auth_config.secret_key,
            ):
                return Response(status_code=403, content="Forbidden: invalid token")

        # Range request
        if range_header:
            content, status_code, headers = await cache_service.handle_range_request(
                path, range_header, dict(request.headers)
            )
            is_hit = False
        else:
            content, status_code, headers, is_hit = await cache_service.get_or_fetch(
                path, dict(request.headers)
            )

        elapsed = time.monotonic() - start_time
        cache_header = "HIT" if is_hit else "MISS"

        # Compression
        content_type = headers.get("content-type", "application/octet-stream")
        if accept_encoding and status_code == 200:
            compressed, encoding = compress_response(
                content, accept_encoding, content_type
            )
            if encoding:
                content = compressed
                headers["Content-Encoding"] = encoding
                headers["Content-Length"] = str(len(content))

        # Response headers
        headers["X-Cache"] = cache_header
        headers["X-Edge-Id"] = edge_id
        headers["X-Response-Time"] = f"{elapsed * 1000:.2f}ms"

        # Record metrics
        metrics_collector.record_request(
            edge_id=edge_id,
            path=path,
            cache_hit=is_hit,
            response_time=elapsed,
            status_code=status_code,
            bytes_sent=len(content),
        )

        return Response(
            content=content,
            status_code=status_code,
            headers=headers,
            media_type=content_type,
        )

    @router.get("/health")
    async def health_check() -> dict:
        """Return edge node health status.

        Returns:
            A dictionary with the health status and edge identifier.
        """
        return {"status": "healthy", "edge_id": edge_id}

    @router.delete("/internal/purge")
    async def purge_cache(
        url: Optional[str] = Query(None),
        prefix: Optional[str] = Query(None),
    ) -> dict:
        """Purge cached entries by exact URL or by key prefix.

        Args:
            url: Exact cache key to purge.
            prefix: Key prefix; all matching entries are removed.

        Returns:
            A dictionary indicating the purge result.
        """
        if url:
            deleted = await cache_store.delete(url)
            return {"purged": deleted, "key": url}
        elif prefix:
            count = await cache_store.delete_by_prefix(prefix)
            return {"purged_count": count, "prefix": prefix}
        return {"error": "Provide 'url' or 'prefix' query parameter"}

    @router.get("/internal/stats")
    async def cache_stats() -> dict:
        """Return cache store statistics.

        Returns:
            A dictionary of cache metrics (entries, size, hit ratio, etc.).
        """
        return await cache_store.stats()

    return router
