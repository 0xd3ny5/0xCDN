"""Origin server FastAPI application factory.

The origin server is the authoritative source of truth for all files
served by the CDN.  It reads files from a configurable assets directory
and serves them with ETag, Range, and Content-Type support.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from presentation.origin.routes import create_router


def create_origin_app(assets_dir: str | None = None) -> FastAPI:
    """Create and configure the origin server application.

    Args:
        assets_dir: Path to the directory containing static assets.
            Falls back to the ``CDN_ASSETS_DIR`` environment variable,
            then defaults to ``./assets``.

    Returns:
        A fully configured ``FastAPI`` application.
    """
    if assets_dir is None:
        assets_dir = os.environ.get("CDN_ASSETS_DIR", "./assets")

    app = FastAPI(
        title="CDN Origin Server",
        description="Source of truth for CDN-served files.",
        version="1.0.0",
    )

    router = create_router(assets_dir)
    app.include_router(router)

    @app.get("/health")
    async def health_check() -> dict:
        return {"status": "healthy", "role": "origin"}

    @app.on_event("startup")
    async def _on_startup() -> None:
        """Log origin server startup details."""
        import logging

        logger = logging.getLogger("cdn.origin")
        logger.info("Origin server started, assets_dir=%s", assets_dir)

    return app
