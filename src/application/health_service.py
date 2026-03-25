"""Periodic health-check service for CDN edge nodes.

Runs a background loop that pings each edge's ``/health`` endpoint and
updates the routing service's view of edge health.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Optional

import httpx

from application.routing_service import RoutingService

logger = logging.getLogger(__name__)


class HealthService:
    """Background service that periodically health-checks edge nodes."""

    def __init__(
        self,
        routing_service: RoutingService,
        check_interval: float = 5.0,
        check_timeout: float = 3.0,
        max_failures: int = 3,
    ) -> None:
        self._routing_service = routing_service
        self._check_interval = check_interval
        self._check_timeout = check_timeout
        self._max_failures = max_failures

        self._failure_counts: dict[str, int] = {}
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Launch the background health-check loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._health_loop())
            logger.info("Health-check loop started (interval=%.1fs)", self._check_interval)

    async def stop(self) -> None:
        """Cancel the background health-check loop."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Health-check loop stopped")

    async def _health_loop(self) -> None:
        """Periodically ping every edge and update routing health."""
        while True:
            edges = self._routing_service.get_all_edges()
            for edge in edges:
                # Run each check with its own timeout so a dead host
                # cannot block checks for other edges.
                asyncio.create_task(
                    self._check_with_timeout(edge.id, edge.host, edge.port)
                )
            await asyncio.sleep(self._check_interval)

    async def _check_with_timeout(
        self, edge_id: str, host: str, port: int
    ) -> None:
        """Run a single health check wrapped in a hard timeout."""
        try:
            await asyncio.wait_for(
                self._check_edge(edge_id, host, port),
                timeout=self._check_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Health check timed out for edge %s", edge_id)
            self._record_failure(edge_id)

    async def _check_edge(
        self,
        edge_id: str,
        host: str,
        port: int,
    ) -> None:
        """Ping a single edge node's /health endpoint."""
        # Resolve DNS in a thread to avoid poisoning the event loop
        if not await self._dns_resolvable(host):
            self._record_failure(edge_id)
            self._update_timestamp(edge_id)
            return

        url = f"http://{host}:{port}/health"
        try:
            async with httpx.AsyncClient(timeout=self._check_timeout) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    self._failure_counts[edge_id] = 0
                    self._routing_service.mark_healthy(edge_id)
                    logger.debug("Edge %s healthy", edge_id)
                else:
                    self._record_failure(edge_id)
        except (httpx.RequestError, Exception) as exc:
            logger.warning(
                "Health check failed for edge %s: %s: %s",
                edge_id, type(exc).__name__, exc,
            )
            self._record_failure(edge_id)

        self._update_timestamp(edge_id)

    def _update_timestamp(self, edge_id: str) -> None:
        for edge in self._routing_service.get_all_edges():
            if edge.id == edge_id:
                edge.last_health_check = time.time()
                break

    @staticmethod
    async def _dns_resolvable(host: str) -> bool:
        """Check if hostname resolves via thread pool (non-blocking)."""
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(
                    None, socket.getaddrinfo, host, None
                ),
                timeout=1.0,
            )
            return True
        except (socket.gaierror, asyncio.TimeoutError, OSError):
            return False

    def _record_failure(self, edge_id: str) -> None:
        """Increment the failure counter and mark unhealthy if threshold reached."""
        self._failure_counts[edge_id] = self._failure_counts.get(edge_id, 0) + 1
        if self._failure_counts[edge_id] >= self._max_failures:
            self._routing_service.mark_unhealthy(edge_id)
            logger.warning(
                "Edge %s marked unhealthy after %d consecutive failures",
                edge_id,
                self._failure_counts[edge_id],
            )
