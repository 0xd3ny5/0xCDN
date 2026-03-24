"""Edge node FastAPI application factory.

Creates a fully wired edge server with cache, origin client, metrics,
circuit breaker, middleware, and routes.  Configuration is read from
environment variables when not explicitly provided.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Optional

from fastapi import FastAPI

from application.cache_service import CacheService
from application.metrics_service import InMemoryMetricsCollector
from config import AuthConfig, CacheConfig, EdgeConfig
from infrastructure.cache.lru_store import LRUCacheStore
from infrastructure.circuit_breaker import CircuitBreaker
from infrastructure.http.origin_client import HttpOriginClient
from infrastructure.http.shield_client import ShieldClient
from infrastructure.logging import CDNLogger
from presentation.edge.middleware import (
    RequestLoggingMiddleware,
    TimingMiddleware,
)
from presentation.edge.routes import create_router

logger = logging.getLogger("src.edge")


def create_edge_app(config: Optional[EdgeConfig] = None) -> FastAPI:
    """Create and configure the edge node application.

    Args:
        config: Edge configuration.  When ``None``, configuration is
            loaded from environment variables via ``EdgeConfig`` defaults.

    Returns:
        A fully configured ``FastAPI`` application ready to serve requests.
    """
    if config is None:
        config = EdgeConfig()

    cache_config = CacheConfig()
    auth_config = AuthConfig()

    # Infrastructure
    cache_store = LRUCacheStore(max_size_bytes=cache_config.max_size_bytes)
    metrics_collector = InMemoryMetricsCollector()
    circuit_breaker = CircuitBreaker(
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_max_calls=1,
        success_threshold=2,
    )

    if config.shield_url:
        origin_client = ShieldClient(shield_url=config.shield_url)
    else:
        origin_client = HttpOriginClient(origin_url=config.origin_url)

    # Application service
    cache_service = CacheService(
        cache_store=cache_store,
        origin_client=origin_client,
        metrics=metrics_collector,
        config=cache_config,
        circuit_breaker=circuit_breaker,
    )

    cdn_logger = CDNLogger(name=f"cdn.edge.{config.edge_id}")

    # Graceful shutdown state
    _active_requests = 0
    _shutting_down = False
    _drain_event = asyncio.Event()
    _drain_event.set()  # not draining initially

    # FastAPI app
    app = FastAPI(
        title=f"CDN Edge - {config.edge_id}",
        description="CDN edge node that caches and serves content.",
        version="1.0.0",
    )

    @app.middleware("http")
    async def graceful_shutdown_middleware(request, call_next):
        """Track active requests for graceful shutdown draining."""
        nonlocal _active_requests
        if _shutting_down and request.url.path != "/health":
            from fastapi.responses import Response
            return Response(status_code=503, content="Shutting down")
        _active_requests += 1
        try:
            response = await call_next(request)
            return response
        finally:
            _active_requests -= 1
            if _shutting_down and _active_requests == 0:
                _drain_event.set()

    # Middleware (order matters: outermost middleware runs first)
    app.add_middleware(
        RequestLoggingMiddleware,
        logger=cdn_logger,
        edge_id=config.edge_id,
    )
    app.add_middleware(
        TimingMiddleware,
        metrics_collector=metrics_collector,
        edge_id=config.edge_id,
    )

    # Routes
    router = create_router(
        cache_service=cache_service,
        metrics_collector=metrics_collector,
        edge_id=config.edge_id,
        cache_store=cache_store,
        auth_config=auth_config,
    )
    app.include_router(router)

    # Store references for potential cleanup
    app.state.origin_client = origin_client
    app.state.cache_store = cache_store
    app.state.cache_service = cache_service
    app.state.metrics_collector = metrics_collector
    app.state.circuit_breaker = circuit_breaker

    @app.on_event("startup")
    async def _on_startup() -> None:
        """Log edge node startup information."""
        logger.info(
            "Edge %s started: region=%s, origin=%s, shield=%s",
            config.edge_id,
            config.region,
            config.origin_url,
            config.shield_url or "none",
        )

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        """Gracefully drain requests, then clean up resources."""
        nonlocal _shutting_down
        _shutting_down = True
        logger.info("Edge %s shutting down, draining %d active requests...", config.edge_id, _active_requests)

        if _active_requests > 0:
            _drain_event.clear()
            try:
                await asyncio.wait_for(_drain_event.wait(), timeout=30.0)
                logger.info("Edge %s drained all requests", config.edge_id)
            except asyncio.TimeoutError:
                logger.warning("Edge %s drain timed out with %d requests remaining", config.edge_id, _active_requests)

        # Cancel background revalidation tasks
        await cache_service.shutdown()

        if hasattr(origin_client, "close"):
            await origin_client.close()
        logger.info("Edge %s shut down", config.edge_id)

    return app
