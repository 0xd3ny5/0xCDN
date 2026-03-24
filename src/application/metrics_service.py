"""In-memory metrics collector.

Implements the ``MetricsCollector`` ABC with thread-safe, per-edge
counters and latency histograms stored entirely in memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from domain.ports import MetricsCollector


@dataclass
class _EdgeMetrics:
    """Internal mutable container for a single edge's raw counters."""

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    response_times: list[float] = field(default_factory=list)
    origin_fetch_times: list[float] = field(default_factory=list)
    bytes_served: int = 0
    bytes_fetched: int = 0


class InMemoryMetricsCollector(MetricsCollector):
    """Collect CDN metrics in memory with asyncio-safe locking."""

    def __init__(self) -> None:
        self._edges: dict[str, _EdgeMetrics] = {}
        self._lock = asyncio.Lock()

    def record_request(
        self,
        edge_id: str,
        path: str,
        cache_hit: bool,
        response_time: float,
        status_code: int,
        bytes_sent: int,
    ) -> None:
        m = self._ensure_edge(edge_id)
        m.total_requests += 1
        if cache_hit:
            m.cache_hits += 1
        else:
            m.cache_misses += 1
        m.response_times.append(response_time)
        m.bytes_served += bytes_sent

    def record_origin_fetch(
        self,
        edge_id: str,
        path: str,
        fetch_time: float,
        bytes_fetched: int,
    ) -> None:
        m = self._ensure_edge(edge_id)
        m.origin_fetch_times.append(fetch_time)
        m.bytes_fetched += bytes_fetched

    def get_edge_metrics(self, edge_id: str) -> dict:
        """Return computed metrics for a single edge node."""
        m = self._edges.get(edge_id)
        if m is None:
            return {}
        return self._compute_metrics(m)

    def get_aggregate_metrics(self) -> dict:
        """Return metrics aggregated across all edge nodes."""
        agg = _EdgeMetrics()
        for m in self._edges.values():
            agg.total_requests += m.total_requests
            agg.cache_hits += m.cache_hits
            agg.cache_misses += m.cache_misses
            agg.response_times.extend(m.response_times)
            agg.origin_fetch_times.extend(m.origin_fetch_times)
            agg.bytes_served += m.bytes_served
            agg.bytes_fetched += m.bytes_fetched
        return self._compute_metrics(agg)

    def _ensure_edge(self, edge_id: str) -> _EdgeMetrics:
        """Return the metrics container for *edge_id*, creating it if needed."""
        if edge_id not in self._edges:
            self._edges[edge_id] = _EdgeMetrics()
        return self._edges[edge_id]

    def _compute_metrics(self, m: _EdgeMetrics) -> dict:
        """Derive high-level stats from raw counters."""
        hit_ratio = (m.cache_hits / m.total_requests) if m.total_requests > 0 else 0.0

        return {
            "total_requests": m.total_requests,
            "cache_hits": m.cache_hits,
            "cache_misses": m.cache_misses,
            "hit_ratio": hit_ratio,
            "bytes_served": m.bytes_served,
            "bytes_fetched": m.bytes_fetched,
            "avg_response_time": self._avg(m.response_times),
            "p50_response_time": self._percentile(m.response_times, 50),
            "p95_response_time": self._percentile(m.response_times, 95),
            "p99_response_time": self._percentile(m.response_times, 99),
            "avg_origin_fetch_time": self._avg(m.origin_fetch_times),
            "p50_origin_fetch_time": self._percentile(m.origin_fetch_times, 50),
            "p95_origin_fetch_time": self._percentile(m.origin_fetch_times, 95),
            "p99_origin_fetch_time": self._percentile(m.origin_fetch_times, 99),
        }

    @staticmethod
    def _avg(data: list[float]) -> float:
        """Return the arithmetic mean, or 0.0 for empty data."""
        return sum(data) / len(data) if data else 0.0

    @staticmethod
    def _percentile(data: list[float], p: float) -> float:
        """Return the *p*-th percentile of *data* using nearest-rank.

        Args:
            data: List of observed values.
            p: Percentile to compute (0-100).

        Returns:
            The percentile value, or 0.0 if *data* is empty.
        """
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = max(0, min(int(len(sorted_data) * p / 100.0), len(sorted_data) - 1))
        return sorted_data[k]
