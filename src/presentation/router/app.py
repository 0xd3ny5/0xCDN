"""Router / load-balancer FastAPI application factory.

Creates the request router that directs traffic to the nearest healthy
edge node with automatic failover.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI

from application.health_service import HealthService
from application.routing_service import RoutingService
from config import RouterConfig
from presentation.router.routes import create_router

logger = logging.getLogger("src.router")


def create_router_app(config: Optional[RouterConfig] = None) -> FastAPI:
    """Create and configure the router / load-balancer application.

    Args:
        config: Router configuration.  When ``None``, configuration is
            loaded from environment variables via ``RouterConfig`` defaults.

    Returns:
        A fully configured ``FastAPI`` application.
    """
    if config is None:
        config = RouterConfig()

    # Application services
    routing_service = RoutingService(edges=config.edges)
    health_service = HealthService(
        routing_service=routing_service,
        check_interval=config.health_check_interval,
        check_timeout=config.health_check_timeout,
        max_failures=config.max_failures,
    )

    app = FastAPI(
        title="CDN Router",
        description="Request router and load balancer for CDN edge nodes.",
        version="1.0.0",
    )

    @app.get("/health")
    async def health_check() -> dict:
        healthy_count = len(routing_service.get_healthy_edges())
        total_count = len(routing_service.get_all_edges())
        return {"status": "healthy", "role": "router", "healthy_edges": healthy_count, "total_edges": total_count}

    router = create_router(
        routing_service=routing_service,
        health_service=health_service,
    )
    app.include_router(router)

    # Store references on app state for access in tests or extensions
    app.state.routing_service = routing_service
    app.state.health_service = health_service

    @app.on_event("startup")
    async def _on_startup() -> None:
        """Start the background health-check loop."""
        await health_service.start()
        edge_ids = [e.id for e in config.edges]
        logger.info(
            "Router started with %d edge(s): %s",
            len(config.edges),
            ", ".join(edge_ids) if edge_ids else "none",
        )

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        """Stop the background health-check loop."""
        await health_service.stop()
        logger.info("Router shut down")

    return app
