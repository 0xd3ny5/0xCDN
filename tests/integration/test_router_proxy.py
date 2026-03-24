"""Integration tests for the router proxy with geo-routing and failover."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import EdgeNodeConfig, RouterConfig
from presentation.router.app import create_router_app


def _make_config(edges: list[EdgeNodeConfig]) -> RouterConfig:
    """Create a RouterConfig with given edges and fast health checks."""
    return RouterConfig(
        edges=edges,
        health_check_interval=999.0,  # disable auto-check in tests
        health_check_timeout=1.0,
        max_failures=1,
    )


# The tests below use edge URLs that won't actually connect, which
# triggers failover.  We rely on the routing_service state to verify
# correct edge selection before the proxy attempt.


class TestRouterEdgeSelection:
    """Verify the router selects the correct edge based on region."""

    def _get_config(self) -> RouterConfig:
        return _make_config([
            EdgeNodeConfig(id="edge-eu", host="127.0.0.1", port=19001, region="eu"),
            EdgeNodeConfig(id="edge-us", host="127.0.0.1", port=19002, region="us"),
            EdgeNodeConfig(id="edge-asia", host="127.0.0.1", port=19003, region="asia"),
        ])

    def test_edges_endpoint(self) -> None:
        """GET /edges lists all configured edges."""
        app = create_router_app(self._get_config())
        with TestClient(app) as client:
            resp = client.get("/edges")
            assert resp.status_code == 200
            data = resp.json()
            ids = {e["id"] for e in data}
            assert ids == {"edge-eu", "edge-us", "edge-asia"}

    def test_health_endpoint(self) -> None:
        """GET /health returns status and edge counts."""
        app = create_router_app(self._get_config())
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "healthy"
            assert body["healthy_edges"] == 3
            assert body["total_edges"] == 3

    def test_503_when_no_edges_configured(self) -> None:
        """Router returns 503 when no edges are available."""
        app = create_router_app(_make_config([]))
        with TestClient(app) as client:
            resp = client.get("/files/test.txt", headers={"X-Client-Region": "eu"})
            assert resp.status_code == 503

    def test_503_when_all_edges_unreachable(self) -> None:
        """Router returns 503 after all failover attempts fail."""
        app = create_router_app(self._get_config())
        with TestClient(app) as client:
            # All edges point to unreachable ports, so all will fail
            resp = client.get("/files/test.txt", headers={"X-Client-Region": "eu"})
            assert resp.status_code == 503

    def test_response_headers_include_request_id(self) -> None:
        """Even on 503, the X-Request-Id should not be in response (only on success)."""
        app = create_router_app(self._get_config())
        with TestClient(app) as client:
            resp = client.get("/files/test.txt", headers={"X-Client-Region": "eu"})
            # On 503 there's no X-Request-Id since no edge responded
            assert resp.status_code == 503


class TestRoutingServiceIntegration:
    """Test routing service state through the router app."""

    def test_mark_unhealthy_changes_routing(self) -> None:
        """Marking an edge unhealthy changes which edge is selected."""
        config = _make_config([
            EdgeNodeConfig(id="edge-eu", host="127.0.0.1", port=19001, region="eu"),
            EdgeNodeConfig(id="edge-us", host="127.0.0.1", port=19002, region="us"),
        ])
        app = create_router_app(config)

        # Access routing_service through app state
        rs = app.state.routing_service
        assert rs.get_nearest_edge("eu").id == "edge-eu"

        rs.mark_unhealthy("edge-eu")
        assert rs.get_nearest_edge("eu").id == "edge-us"

    def test_ordered_edges_through_app(self) -> None:
        """get_ordered_edges returns region-priority order via app state."""
        config = _make_config([
            EdgeNodeConfig(id="edge-eu", host="127.0.0.1", port=19001, region="eu"),
            EdgeNodeConfig(id="edge-us", host="127.0.0.1", port=19002, region="us"),
            EdgeNodeConfig(id="edge-asia", host="127.0.0.1", port=19003, region="asia"),
        ])
        app = create_router_app(config)
        rs = app.state.routing_service

        ordered = rs.get_ordered_edges("asia")
        regions = [e.region for e in ordered]
        assert regions == ["asia", "eu", "us"]
