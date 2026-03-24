"""CDN configuration dataclasses.

All configuration is loaded from environment variables with sensible
defaults.  No third-party dependencies are used -- only the standard
library ``os`` and ``dataclasses`` modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CacheConfig:
    """Configuration for the two-tier (hot + cold) cache.

    Attributes:
        max_size_bytes: Maximum total cache size in bytes (hot + cold).
        default_ttl: Default time-to-live for cache entries, in seconds.
        hot_tier_max_items: Maximum number of entries in the hot (in-memory)
            tier before eviction to the cold tier.
        cold_tier_path: Filesystem path used for cold-tier storage.
    """

    max_size_bytes: int = int(
        os.environ.get("CDN_CACHE_MAX_SIZE_BYTES", str(100 * 1024 * 1024))
    )
    default_ttl: float = float(
        os.environ.get("CDN_CACHE_DEFAULT_TTL", "3600")
    )
    hot_tier_max_items: int = int(
        os.environ.get("CDN_CACHE_HOT_TIER_MAX_ITEMS", "1000")
    )
    cold_tier_path: str = os.environ.get(
        "CDN_CACHE_COLD_TIER_PATH", "/tmp/cdn_cold"
    )


@dataclass
class EdgeConfig:
    """Configuration for a single CDN edge server.

    Attributes:
        edge_id: Unique identifier for this edge node.
        host: Hostname or IP address the edge listens on.
        port: Port number the edge listens on.
        region: Geographic region code (e.g. ``"us-east-1"``).
        origin_url: Base URL of the upstream origin server.
        shield_url: Optional URL of a mid-tier shield/cache node that sits
            between this edge and the origin.
    """

    edge_id: str = os.environ.get("CDN_EDGE_ID", "edge-1")
    host: str = os.environ.get("CDN_EDGE_HOST", "0.0.0.0")
    port: int = int(os.environ.get("CDN_EDGE_PORT", "8080"))
    region: str = os.environ.get("CDN_EDGE_REGION", "us-east-1")
    origin_url: str = os.environ.get("CDN_ORIGIN_URL", "http://localhost:9000")
    shield_url: Optional[str] = os.environ.get("CDN_SHIELD_URL")


@dataclass
class EdgeNodeConfig:
    """Lightweight description of an edge node used by the router.

    Attributes:
        id: Unique identifier for the edge node.
        host: Hostname or IP address.
        port: Port number.
        region: Geographic region code.
    """

    id: str
    host: str
    port: int
    region: str


def _parse_edge_nodes() -> list[EdgeNodeConfig]:
    """Parse edge node definitions from the ``CDN_ROUTER_EDGES`` env var.

    Expected format::

        id1:host1:port1:region1,id2:host2:port2:region2,...

    Returns an empty list when the variable is not set.
    """
    raw = os.environ.get("CDN_ROUTER_EDGES", "")
    if not raw.strip():
        return []
    nodes: list[EdgeNodeConfig] = []
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) != 4:
            continue
        node_id, host, port_str, region = parts
        nodes.append(
            EdgeNodeConfig(
                id=node_id.strip(),
                host=host.strip(),
                port=int(port_str.strip()),
                region=region.strip(),
            )
        )
    return nodes


@dataclass
class RouterConfig:
    """Configuration for the request router / load balancer.

    Attributes:
        edges: List of edge nodes the router can forward requests to.
        health_check_interval: Seconds between periodic health checks.
        health_check_timeout: Seconds before a health check is considered
            failed.
        max_failures: Number of consecutive failures before an edge is
            marked unhealthy.
    """

    edges: list[EdgeNodeConfig] = field(default_factory=_parse_edge_nodes)
    health_check_interval: float = float(
        os.environ.get("CDN_HEALTH_CHECK_INTERVAL", "5")
    )
    health_check_timeout: float = float(
        os.environ.get("CDN_HEALTH_CHECK_TIMEOUT", "1")
    )
    max_failures: int = int(
        os.environ.get("CDN_HEALTH_MAX_FAILURES", "3")
    )


@dataclass
class ManagementConfig:
    """Configuration for the management / admin API.

    Attributes:
        host: Hostname or IP address the management API listens on.
        port: Port number for the management API.
        edge_urls: List of base URLs for all edge nodes that this
            management server can control.
    """

    host: str = os.environ.get("CDN_MGMT_HOST", "0.0.0.0")
    port: int = int(os.environ.get("CDN_MGMT_PORT", "9090"))
    edge_urls: list[str] = field(default_factory=lambda: _parse_list("CDN_MGMT_EDGE_URLS"))


def _parse_list(env_var: str) -> list[str]:
    """Split a comma-separated environment variable into a list of strings."""
    raw = os.environ.get(env_var, "")
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class AuthConfig:
    """Configuration for JWT-based authentication.

    Attributes:
        secret_key: Secret used to sign and verify tokens.
        token_ttl: Token lifetime in seconds.
    """

    secret_key: str = os.environ.get("CDN_AUTH_SECRET_KEY", "change-me-in-production")
    token_ttl: int = int(os.environ.get("CDN_AUTH_TOKEN_TTL", "3600"))
