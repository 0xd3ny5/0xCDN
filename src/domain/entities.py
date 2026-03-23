"""Core domain entities for the CDN system."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CacheEntry:
    """Represents a cached response stored in the CDN.

    Attributes:
        content: The raw response body bytes.
        headers: HTTP headers associated with the cached response.
        etag: Entity tag for conditional request validation, if present.
        created_at: Unix timestamp when the entry was first cached.
        ttl: Time-to-live in seconds before the entry is considered stale.
        last_accessed: Unix timestamp of the most recent access.
        size: Size of the content in bytes.
        status_code: HTTP status code of the original response.
    """
    content: bytes
    headers: dict[str, str]
    etag: Optional[str]
    created_at: float
    ttl: float
    last_accessed: float
    size: int
    status_code: int
    stale_while_revalidate: float = 0.0
    vary_headers: Optional[list[str]] = None

    def is_expired(self) -> bool:
        """Check whether this cache entry has exceeded its TTL."""
        return (time.time() - self.created_at) > self.ttl

    def is_stale_servable(self) -> bool:
        """Check if expired but still within stale-while-revalidate window."""
        age = time.time() - self.created_at
        return age > self.ttl and age <= (self.ttl + self.stale_while_revalidate)

    def touch(self) -> None:
        """Update the last-accessed timestamp to the current time."""
        self.last_accessed = time.time()


@dataclass
class EdgeNode:
    """Represents a CDN edge server in the network.

    Attributes:
        id: Unique identifier for the edge node.
        host: Hostname or IP address the edge listens on.
        port: Port number the edge listens on.
        region: Geographic region code (e.g. ``"us-east-1"``).
        healthy: Whether the node is currently considered healthy.
        last_health_check: Unix timestamp of the last health check, or None
                           if no check has been performed yet.
    """
    id: str
    host: str
    port: int
    region: str
    healthy: bool = True
    last_health_check: Optional[float] = None


@dataclass
class OriginResponse:
    """Encapsulates a response received from the origin server.

    Attributes:
        content: The raw response body bytes.
        status_code: HTTP status code returned by the origin.
        headers: Response headers from the origin server.
        etag: Entity tag header value, if present in the response.
    """
    content: bytes
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    etag: Optional[str] = None