"""Management API FastAPI application factory.

Provides cache purge, cache warming, metrics aggregation, and a simple
dashboard for CDN operators.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI

from application.purge_service import PurgeService
from application.warm_service import WarmService
from config import ManagementConfig
from presentation.management.routes import create_router

logger = logging.getLogger("src.management")


def create_management_app(
    config: Optional[ManagementConfig] = None,
) -> FastAPI:
    """Create and configure the management API application.

    Args:
        config: Management configuration.  When ``None``, configuration
            is loaded from environment variables via ``ManagementConfig``
            defaults.

    Returns:
        A fully configured ``FastAPI`` application.
    """
    if config is None:
        config = ManagementConfig()

    # Application services
    purge_service = PurgeService(edge_urls=config.edge_urls)
    warm_service = WarmService(edge_urls=config.edge_urls)

    app = FastAPI(
        title="CDN Management API",
        description="Administration interface for cache purge, warming, and metrics.",
        version="1.0.0",
    )

    @app.get("/health")
    async def health_check() -> dict:
        return {"status": "healthy", "role": "management"}

    router = create_router(
        purge_service=purge_service,
        warm_service=warm_service,
        edge_urls=config.edge_urls,
    )
    app.include_router(router)

    # Store references for tests or extensions
    app.state.purge_service = purge_service
    app.state.warm_service = warm_service

    @app.on_event("startup")
    async def _on_startup() -> None:
        """Log management API startup information."""
        logger.info(
            "Management API started, controlling %d edge(s): %s",
            len(config.edge_urls),
            ", ".join(config.edge_urls) if config.edge_urls else "none",
        )

    return app
