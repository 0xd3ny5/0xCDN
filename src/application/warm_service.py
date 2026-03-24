"""Cache warming service.

Pre-populates edge caches by issuing GET requests for a list of URLs
against every configured edge node.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class WarmService:
    """Pre-warm CDN edge caches by fetching specified URLs."""

    def __init__(self, edge_urls: list[str]) -> None:
        self._edge_urls = edge_urls

    async def warm(self, urls: list[str]) -> dict[str, list[str]]:
        """Warm each edge's cache for every URL in *urls*.

        Issues GET requests in parallel using ``asyncio.gather``.

        Args:
            urls: List of URLs to pre-populate in the caches.

        Returns:
            A mapping of ``{edge_url: [list of successfully warmed URLs]}``.
        """
        results: dict[str, list[str]] = {edge: [] for edge in self._edge_urls}

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [
                self._warm_single(client, edge_url, url, results)
                for edge_url in self._edge_urls
                for url in urls
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        return results

    @staticmethod
    async def _warm_single(
        client: httpx.AsyncClient,
        edge_url: str,
        url: str,
        results: dict[str, list[str]],
    ) -> None:
        """Send a single warming GET request."""
        target = f"{edge_url}{url}"
        try:
            response = await client.get(target)
            if response.status_code in (200, 304):
                results[edge_url].append(url)
                logger.debug("Warmed %s on %s", url, edge_url)
            else:
                logger.warning(
                    "Warm request %s on %s returned %d",
                    url,
                    edge_url,
                    response.status_code,
                )
        except (httpx.RequestError, Exception) as exc:
            logger.warning("Warm request %s on %s failed: %s", url, edge_url, exc)
