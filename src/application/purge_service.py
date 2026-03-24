"""Cache purge service.

Sends purge requests to all configured edge nodes to invalidate cached
content by exact URL or by URL prefix.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class PurgeService:
    """Purge cached content across all CDN edge nodes."""

    def __init__(self, edge_urls: list[str]) -> None:
        self._edge_urls = edge_urls

    async def purge_url(self, url: str) -> dict[str, bool]:
        """Purge a single URL from every edge's cache.

        Args:
            url: The URL to purge.

        Returns:
            A mapping of ``{edge_url: success}`` indicating which edges
            acknowledged the purge.
        """
        results: dict[str, bool] = {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            for edge_url in self._edge_urls:
                endpoint = f"{edge_url}/internal/purge?url={quote(url, safe='')}"
                try:
                    response = await client.delete(endpoint)
                    results[edge_url] = response.status_code in (200, 204)
                except (httpx.RequestError, Exception) as exc:
                    logger.warning("Purge failed for %s: %s", edge_url, exc)
                    results[edge_url] = False
        return results

    async def purge_prefix(self, prefix: str) -> dict[str, bool]:
        """Purge all URLs matching a prefix from every edge's cache.

        Args:
            prefix: The URL prefix to purge.

        Returns:
            A mapping of ``{edge_url: success}`` indicating which edges
            acknowledged the purge.
        """
        results: dict[str, bool] = {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            for edge_url in self._edge_urls:
                endpoint = f"{edge_url}/internal/purge?prefix={quote(prefix, safe='')}"
                try:
                    response = await client.delete(endpoint)
                    results[edge_url] = response.status_code in (200, 204)
                except (httpx.RequestError, Exception) as exc:
                    logger.warning("Prefix purge failed for %s: %s", edge_url, exc)
                    results[edge_url] = False
        return results
