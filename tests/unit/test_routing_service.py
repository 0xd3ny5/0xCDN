"""Unit tests for RoutingService with region-priority based routing."""

from __future__ import annotations

from application.routing_service import DEFAULT_REGION, REGION_PRIORITY, RoutingService
from config import EdgeNodeConfig


def _make_geo_edges() -> list[EdgeNodeConfig]:
    """Create edge nodes matching the docker-compose geo setup."""
    return [
        EdgeNodeConfig(id="edge-eu", host="edge-eu", port=8001, region="eu"),
        EdgeNodeConfig(id="edge-us", host="edge-us", port=8002, region="us"),
        EdgeNodeConfig(id="edge-asia", host="edge-asia", port=8003, region="asia"),
    ]


# ------------------------------------------------------------------
# AC-1: Region-based routing
# ------------------------------------------------------------------


def test_eu_client_routes_to_eu_edge() -> None:
    """Client from EU is routed to the EU edge."""
    service = RoutingService(_make_geo_edges())
    edge = service.get_nearest_edge("eu")
    assert edge is not None
    assert edge.id == "edge-eu"
    assert edge.region == "eu"


def test_us_client_routes_to_us_edge() -> None:
    """Client from US is routed to the US edge."""
    service = RoutingService(_make_geo_edges())
    edge = service.get_nearest_edge("us")
    assert edge is not None
    assert edge.id == "edge-us"
    assert edge.region == "us"


def test_asia_client_routes_to_asia_edge() -> None:
    """Client from Asia is routed to the Asia edge."""
    service = RoutingService(_make_geo_edges())
    edge = service.get_nearest_edge("asia")
    assert edge is not None
    assert edge.id == "edge-asia"
    assert edge.region == "asia"


# ------------------------------------------------------------------
# AC-2: Fallback by proximity
# ------------------------------------------------------------------


def test_eu_fallback_to_us_when_eu_down() -> None:
    """If EU edge is down, EU client falls back to US (next in priority)."""
    service = RoutingService(_make_geo_edges())
    service.mark_unhealthy("edge-eu")
    edge = service.get_nearest_edge("eu")
    assert edge is not None
    assert edge.id == "edge-us"
    assert edge.region == "us"


def test_us_fallback_to_eu_when_us_down() -> None:
    """If US edge is down, US client falls back to EU."""
    service = RoutingService(_make_geo_edges())
    service.mark_unhealthy("edge-us")
    edge = service.get_nearest_edge("us")
    assert edge is not None
    assert edge.id == "edge-eu"
    assert edge.region == "eu"


def test_asia_fallback_chain() -> None:
    """Asia client falls back to EU, then US."""
    service = RoutingService(_make_geo_edges())
    service.mark_unhealthy("edge-asia")
    edge = service.get_nearest_edge("asia")
    assert edge is not None
    assert edge.id == "edge-eu"  # asia -> eu -> us

    service.mark_unhealthy("edge-eu")
    edge = service.get_nearest_edge("asia")
    assert edge is not None
    assert edge.id == "edge-us"


# ------------------------------------------------------------------
# AC-3: No routing to unhealthy edge
# ------------------------------------------------------------------


def test_unhealthy_edge_never_selected() -> None:
    """An unhealthy edge must not be selected."""
    service = RoutingService(_make_geo_edges())
    service.mark_unhealthy("edge-eu")
    # Even though EU is first priority for EU client, it should be skipped
    edge = service.get_nearest_edge("eu")
    assert edge is not None
    assert edge.id != "edge-eu"


# ------------------------------------------------------------------
# AC-6: Health state refresh
# ------------------------------------------------------------------


def test_recovered_edge_is_used_again() -> None:
    """After recovery, an edge is used again."""
    service = RoutingService(_make_geo_edges())
    service.mark_unhealthy("edge-eu")
    assert service.get_nearest_edge("eu").id == "edge-us"

    service.mark_healthy("edge-eu")
    assert service.get_nearest_edge("eu").id == "edge-eu"


# ------------------------------------------------------------------
# AC-7: All edges down → None
# ------------------------------------------------------------------


def test_all_unhealthy_returns_none() -> None:
    """When every edge is unhealthy, get_nearest_edge returns None."""
    service = RoutingService(_make_geo_edges())
    for e in _make_geo_edges():
        service.mark_unhealthy(e.id)
    assert service.get_nearest_edge("eu") is None


# ------------------------------------------------------------------
# Unknown region
# ------------------------------------------------------------------


def test_unknown_region_uses_default_chain() -> None:
    """An unknown region like 'mars' uses the default region chain."""
    service = RoutingService(_make_geo_edges())
    edge = service.get_nearest_edge("mars")
    assert edge is not None
    # Default region is 'eu', so should pick eu edge
    assert edge.region == "eu"


# ------------------------------------------------------------------
# get_ordered_edges
# ------------------------------------------------------------------


def test_ordered_edges_respects_priority() -> None:
    """get_ordered_edges returns edges in region-priority order."""
    service = RoutingService(_make_geo_edges())
    ordered = service.get_ordered_edges("eu")
    regions = [e.region for e in ordered]
    assert regions == ["eu", "us", "asia"]


def test_ordered_edges_skips_unhealthy() -> None:
    """get_ordered_edges excludes unhealthy edges."""
    service = RoutingService(_make_geo_edges())
    service.mark_unhealthy("edge-eu")
    ordered = service.get_ordered_edges("eu")
    ids = [e.id for e in ordered]
    assert "edge-eu" not in ids
    assert ids == ["edge-us", "edge-asia"]


def test_ordered_edges_empty_when_all_down() -> None:
    """get_ordered_edges returns empty list when all edges down."""
    service = RoutingService(_make_geo_edges())
    for e in _make_geo_edges():
        service.mark_unhealthy(e.id)
    assert service.get_ordered_edges("eu") == []


# ------------------------------------------------------------------
# Region priority table
# ------------------------------------------------------------------


def test_region_priority_table_completeness() -> None:
    """Every region in REGION_PRIORITY covers all three regions."""
    for region, chain in REGION_PRIORITY.items():
        assert region == chain[0], f"{region} should be first in its own chain"
        assert set(chain) == {"eu", "us", "asia"}


def test_default_region_is_eu() -> None:
    """Default region should be 'eu' per spec."""
    assert DEFAULT_REGION == "eu"


# ------------------------------------------------------------------
# Multiple edges per region
# ------------------------------------------------------------------


def test_multiple_edges_same_region_picks_first_healthy() -> None:
    """When multiple edges exist in a region, first healthy is picked."""
    edges = [
        EdgeNodeConfig(id="edge-eu-1", host="h1", port=8001, region="eu"),
        EdgeNodeConfig(id="edge-eu-2", host="h2", port=8002, region="eu"),
        EdgeNodeConfig(id="edge-us-1", host="h3", port=8003, region="us"),
    ]
    service = RoutingService(edges)
    assert service.get_nearest_edge("eu").id == "edge-eu-1"

    service.mark_unhealthy("edge-eu-1")
    assert service.get_nearest_edge("eu").id == "edge-eu-2"

    service.mark_unhealthy("edge-eu-2")
    assert service.get_nearest_edge("eu").id == "edge-us-1"
