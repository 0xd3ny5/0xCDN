"""Router / load-balancer routes.

Proxies incoming requests to the nearest healthy edge node based on
the client's region header, with automatic failover ordered by the
region-proximity table.  Each request is logged with structured fields
for observability.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

from application.health_service import HealthService
from application.routing_service import DEFAULT_REGION, RoutingService

logger = logging.getLogger("cdn.router")

# Headers that MUST NOT be forwarded between hops (RFC 7230 §6.1).
HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})

# Maximum number of failover attempts before returning 503.
MAX_FAILOVER_ATTEMPTS = 2


def create_router(
    routing_service: RoutingService,
    health_service: HealthService,
) -> APIRouter:
    """Create the router / load-balancer API router."""
    router = APIRouter()

    @router.get("/edges")
    async def list_edges() -> list[dict]:
        """List all configured edge nodes with their health status."""
        edges = routing_service.get_all_edges()
        return [
            {
                "id": edge.id,
                "host": edge.host,
                "port": edge.port,
                "region": edge.region,
                "healthy": edge.healthy,
                "last_health_check": edge.last_health_check,
            }
            for edge in edges
        ]

    @router.get("/files/{path:path}")
    async def proxy_file(
        path: str,
        request: Request,
        x_client_region: Optional[str] = Header(None, alias="x-client-region"),
    ) -> Response:
        """Proxy a file request to the nearest healthy edge."""
        region = (x_client_region or DEFAULT_REGION).lower().strip()
        return await _proxy_to_edge(request, path, region, routing_service)

    @router.api_route("/{path:path}", methods=["GET"])
    async def catch_all_proxy(
        path: str,
        request: Request,
        x_client_region: Optional[str] = Header(None, alias="x-client-region"),
    ) -> Response:
        """Catch-all proxy for any GET path."""
        region = (x_client_region or DEFAULT_REGION).lower().strip()
        return await _proxy_to_edge(request, path, region, routing_service)

    return router


def _filter_request_headers(headers: dict[str, str]) -> dict[str, str]:
    """Forward only safe (non hop-by-hop) headers from the client."""
    filtered: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_HEADERS or lower in ("host", "x-client-region"):
            continue
        filtered[key] = value
    return filtered


def _filter_response_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop and internal headers from the edge response."""
    safe: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in HOP_BY_HOP_HEADERS:
            continue
        safe[key] = value
    return safe


async def _proxy_to_edge(
    request: Request,
    path: str,
    region: str,
    routing_service: RoutingService,
) -> Response:
    """Forward the request to a healthy edge with ordered failover.

    Uses the region-proximity table from RoutingService to determine
    the order of edges to try.  Stops after ``MAX_FAILOVER_ATTEMPTS``
    failovers (so at most MAX_FAILOVER_ATTEMPTS + 1 total attempts).
    """
    request_id = uuid.uuid4().hex[:12]
    start = time.monotonic()

    ordered_edges = routing_service.get_ordered_edges(region)
    if not ordered_edges:
        _log_request(
            request_id=request_id,
            client_region=region,
            selected_edge_id=None,
            selected_edge_region=None,
            failover_count=0,
            response_status=503,
            response_time_ms=_elapsed_ms(start),
        )
        return Response(status_code=503, content="No healthy edge nodes available")

    # Limit total attempts: first try + MAX_FAILOVER_ATTEMPTS retries
    max_attempts = min(len(ordered_edges), MAX_FAILOVER_ATTEMPTS + 1)
    forward_headers = _filter_request_headers(dict(request.headers))
    failover_count = 0
    selected_edge = ordered_edges[0]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for i in range(max_attempts):
            edge = ordered_edges[i]
            selected_edge = edge
            edge_url = f"http://{edge.host}:{edge.port}/{path.lstrip('/')}"
            try:
                response = await client.get(
                    edge_url,
                    headers=forward_headers,
                    params=dict(request.query_params),
                )

                resp_headers = _filter_response_headers(dict(response.headers))
                resp_headers["X-CDN-Edge"] = edge.id
                resp_headers["X-CDN-Region"] = edge.region
                resp_headers["X-Request-Id"] = request_id

                _log_request(
                    request_id=request_id,
                    client_region=region,
                    selected_edge_id=edge.id,
                    selected_edge_region=edge.region,
                    failover_count=failover_count,
                    response_status=response.status_code,
                    response_time_ms=_elapsed_ms(start),
                )

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=resp_headers,
                    media_type=response.headers.get("content-type"),
                )

            except (httpx.RequestError, Exception) as exc:
                logger.warning(
                    "Edge %s failed for /%s: %s",
                    edge.id, path, exc,
                )
                routing_service.mark_unhealthy(edge.id)
                failover_count += 1
                continue

    _log_request(
        request_id=request_id,
        client_region=region,
        selected_edge_id=selected_edge.id if selected_edge else None,
        selected_edge_region=selected_edge.region if selected_edge else None,
        failover_count=failover_count,
        response_status=503,
        response_time_ms=_elapsed_ms(start),
    )
    return Response(
        status_code=503,
        content="All edge nodes failed to serve the request",
    )


def _elapsed_ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 2)


def _log_request(
    *,
    request_id: str,
    client_region: str,
    selected_edge_id: str | None,
    selected_edge_region: str | None,
    failover_count: int,
    response_status: int,
    response_time_ms: float,
) -> None:
    """Emit a structured log entry for every proxied request."""
    logger.info(
        "routed request_id=%s region=%s edge=%s edge_region=%s failovers=%d status=%d time=%.2fms",
        request_id,
        client_region,
        selected_edge_id or "none",
        selected_edge_region or "none",
        failover_count,
        response_status,
        response_time_ms,
    )
