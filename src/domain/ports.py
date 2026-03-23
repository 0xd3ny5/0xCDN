from __future__ import annotations

__all__: tuple[str, ...] = (
    "CacheStore",
    "OriginClient",
    "MetricsCollector",
)

from abc import ABC, abstractmethod
from typing import Optional

from cdn.domain.entities import CacheEntry, OriginResponse


class CacheStore(ABC):
    """Interface for cache storage backends.

    Although the cache is in-memory, it is used in an async environment.
    The API is async to support asyncio.Lock, which allows safe concurrent
    access without blocking the event loop.

    This avoids race conditions and prevents blocking the server under load.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[CacheEntry]:
        """Retrieve a cache entry by its string key.

        Args:
            key: The cache key to look up.

        Returns:
            The cached entry, or ``None`` if no entry exists for *key*.
        """

    @abstractmethod
    async def put(self, key: str, entry: CacheEntry) -> None:
        """Store or overwrite a cache entry.

        Args:
            key: The cache key to associate with *entry*.
            entry: The cache entry to store.
        """

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Remove a single cache entry.

        Args:
            key: The cache key to remove.

        Returns:
            ``True`` if an entry was removed, ``False`` if the key was not
            found.
        """

    @abstractmethod
    async def delete_by_prefix(self, prefix: str) -> int:
        """Remove all entries whose key starts with *prefix*.

        Args:
            prefix: The key prefix to match against.

        Returns:
            The number of entries that were removed.
        """

    @abstractmethod
    async def keys(self) -> list[str]:
        """Return all keys currently stored in the cache.

        Returns:
            A list of cache key strings.
        """

    @abstractmethod
    async def stats(self) -> dict:  # TODO: TypedDict
        """Return runtime statistics about the cache.

        Returns:
            A dictionary containing implementation-specific metrics such as
            total entries, total size, hit/miss counts, etc.
        """


class OriginClient(ABC):
    """Interface for fetching content from the upstream origin server."""

    @abstractmethod
    async def fetch(
        self,
        path: str,
        headers: Optional[dict[str, str]] = None,
    ) -> OriginResponse:
        """Fetch a resource from the origin.

        Args:
            path: The URL path to request from the origin.
            headers: Optional extra headers to forward (e.g. conditional
                request headers such as ``If-None-Match``).

        Returns:
            An ``OriginResponse`` containing the origin's reply.
        """


class MetricsCollector(ABC):
    """Interface for recording and querying CDN operational metrics."""

    @abstractmethod
    def record_request(
        self,
        edge_id: str,
        path: str,
        cache_hit: bool,
        response_time: float,
        status_code: int,
        bytes_sent: int,
    ) -> None:
        """Record an incoming client request handled by an edge node.

        Args:
            edge_id: Identifier of the edge node that served the request.
            path: The requested URL path.
            cache_hit: Whether the response was served from cache.
            response_time: Total time to handle the request, in seconds.
            status_code: HTTP status code returned to the client.
            bytes_sent: Number of bytes in the response body.
        """

    @abstractmethod
    def record_origin_fetch(
        self,
        edge_id: str,
        path: str,
        fetch_time: float,
        bytes_fetched: int,
    ) -> None:
        """Record a fetch from the origin server triggered by a cache miss.

        Args:
            edge_id: Identifier of the edge node that performed the fetch.
            path: The URL path fetched from the origin.
            fetch_time: Time taken to complete the origin fetch, in seconds.
            bytes_fetched: Number of bytes received from the origin.
        """

    @abstractmethod
    def get_edge_metrics(self, edge_id: str) -> dict:  # TODO: TypedDict
        """Retrieve metrics for a specific edge node.

        Args:
            edge_id: Identifier of the edge node.

        Returns:
            A dictionary of metric names to values for the given edge.
        """

    @abstractmethod
    def get_aggregate_metrics(self) -> dict:  # TODO: TypedDict
        """Retrieve aggregated metrics across all edge nodes.

        Returns:
            A dictionary of metric names to aggregate values.
        """
