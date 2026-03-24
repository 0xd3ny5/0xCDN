"""Edge node middleware.

Provides structured request logging and response timing for the edge
server.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from domain.ports import MetricsCollector
from infrastructure.logging import CDNLogger


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request as structured JSON using the CDN logger.

    Args:
        app: The ASGI application.
        logger: A ``CDNLogger`` instance used for structured output.
        edge_id: Identifier for the edge node (included in log entries).
    """

    def __init__(self, app: object, logger: CDNLogger, edge_id: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._logger = logger
        self._edge_id = edge_id

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the request, log its details, and return the response."""
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        cache_header = response.headers.get("X-Cache", "UNKNOWN")
        cache_hit = cache_header == "HIT"

        # Read Content-Length if set, otherwise 0
        bytes_sent = int(response.headers.get("content-length", 0))

        self._logger.request(
            edge_id=self._edge_id,
            path=request.url.path,
            cache_hit=cache_hit,
            response_time=elapsed,
            status_code=response.status_code,
            bytes_sent=bytes_sent,
        )

        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """Add an ``X-Response-Time`` header and record timing to metrics.

    Args:
        app: The ASGI application.
        metrics_collector: Collector for recording timing information.
        edge_id: Identifier for the edge node.
    """

    def __init__(
        self,
        app: object,
        metrics_collector: MetricsCollector,
        edge_id: str,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._metrics = metrics_collector
        self._edge_id = edge_id

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Time the request and attach the duration as a response header."""
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        response.headers["X-Response-Time"] = f"{elapsed * 1000:.2f}ms"

        return response
