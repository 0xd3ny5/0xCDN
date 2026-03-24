"""Request routing service.

Routes incoming requests to the nearest healthy edge node based on
the client's geographic region using a proximity table for ordered
failover across regions.
"""

from __future__ import annotations

from config import EdgeNodeConfig
from domain.entities import EdgeNode


# Explicit region-proximity table.  When a client's region is not
# found here, we fall back to the default chain.
REGION_PRIORITY: dict[str, list[str]] = {
    "eu": ["eu", "us", "asia"],
    "us": ["us", "eu", "asia"],
    "asia": ["asia", "eu", "us"],
}

DEFAULT_REGION = "eu"


class RoutingService:
    """Route requests to the nearest healthy CDN edge node.

    Uses a region-proximity table so that failover follows a
    deterministic geographic order rather than falling back to an
    arbitrary healthy node.
    """

    def __init__(
        self,
        edges: list[EdgeNodeConfig],
        region_priority: dict[str, list[str]] | None = None,
    ) -> None:
        self._edges: list[EdgeNode] = [
            EdgeNode(
                id=e.id,
                host=e.host,
                port=e.port,
                region=e.region,
            )
            for e in edges
        ]
        self._edge_status: dict[str, bool] = {e.id: True for e in self._edges}
        self._region_priority = region_priority or REGION_PRIORITY

    def get_nearest_edge(self, client_region: str) -> EdgeNode | None:
        """Return the best edge node for *client_region*.

        Selection strategy uses the region-proximity table:
        1. Walk preferred regions in priority order.
        2. Within each region pick the first healthy edge (by config order / priority).
        3. Return ``None`` if every edge is unhealthy.
        """
        preferred = self._region_priority.get(
            client_region,
            self._region_priority.get(DEFAULT_REGION, []),
        )
        for region in preferred:
            for edge in self._edges:
                if edge.region == region and self._edge_status.get(edge.id, False):
                    return edge

        # If the priority list didn't cover all configured regions, fall
        # back to any remaining healthy edge.
        for edge in self._edges:
            if self._edge_status.get(edge.id, False):
                return edge

        return None

    def get_ordered_edges(self, client_region: str) -> list[EdgeNode]:
        """Return all healthy edges ordered by proximity to *client_region*.

        Used by the router to iterate for failover.
        """
        preferred = self._region_priority.get(
            client_region,
            self._region_priority.get(DEFAULT_REGION, []),
        )
        ordered: list[EdgeNode] = []
        seen: set[str] = set()

        for region in preferred:
            for edge in self._edges:
                if edge.region == region and self._edge_status.get(edge.id, False) and edge.id not in seen:
                    ordered.append(edge)
                    seen.add(edge.id)

        # Append any remaining healthy edges not yet included
        for edge in self._edges:
            if self._edge_status.get(edge.id, False) and edge.id not in seen:
                ordered.append(edge)
                seen.add(edge.id)

        return ordered

    def mark_unhealthy(self, edge_id: str) -> None:
        """Mark an edge node as unhealthy."""
        self._edge_status[edge_id] = False
        for edge in self._edges:
            if edge.id == edge_id:
                edge.healthy = False
                break

    def mark_healthy(self, edge_id: str) -> None:
        """Mark an edge node as healthy."""
        self._edge_status[edge_id] = True
        for edge in self._edges:
            if edge.id == edge_id:
                edge.healthy = True
                break

    def get_healthy_edges(self) -> list[EdgeNode]:
        """Return all edges currently marked as healthy."""
        return [e for e in self._edges if self._edge_status.get(e.id, False)]

    def get_all_edges(self) -> list[EdgeNode]:
        """Return every configured edge node."""
        return list(self._edges)
