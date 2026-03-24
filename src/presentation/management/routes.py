"""Management API routes.

Provides cache purge, cache warm, metrics aggregation, and a simple
HTML dashboard for CDN operators.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Query
from fastapi.responses import HTMLResponse

from application.purge_service import PurgeService
from application.warm_service import WarmService

logger = logging.getLogger("src.management")


def create_router(
    purge_service: PurgeService,
    warm_service: WarmService,
    edge_urls: list[str],
) -> APIRouter:
    """Create the management API router.

    Args:
        purge_service: Service for invalidating cached content across edges.
        warm_service: Service for pre-populating edge caches.
        edge_urls: Base URLs of all edge nodes for stats collection.

    Returns:
        A configured ``APIRouter`` with purge, warm, metrics, and
        dashboard endpoints.
    """
    router = APIRouter()

    @router.delete("/cache")
    async def purge_cache(
        url: Optional[str] = Query(None),
        prefix: Optional[str] = Query(None),
    ) -> dict:
        """Purge cached content by exact URL or prefix.

        Args:
            url: Exact URL to purge from all edge caches.
            prefix: URL prefix; all matching entries are purged.

        Returns:
            A mapping of ``{edge_url: success}`` for each edge.
        """
        if url:
            results = await purge_service.purge_url(url)
        elif prefix:
            results = await purge_service.purge_prefix(prefix)
        else:
            return {"error": "Provide 'url' or 'prefix' query parameter"}
        return results

    @router.post("/cache/warm")
    async def warm_cache(
        body: dict = Body(..., examples=[{"urls": ["/files/image.png"]}]),
    ) -> dict:
        """Pre-warm edge caches for the specified URLs.

        Expects a JSON body with a ``urls`` list.

        Returns:
            A mapping of ``{edge_url: [successfully warmed URLs]}`` for each
            edge.
        """
        urls = body.get("urls", [])
        if not urls:
            return {"error": "Provide a non-empty 'urls' list in the request body"}
        results = await warm_service.warm(urls)
        return results

    @router.get("/metrics")
    async def get_metrics() -> dict:
        """Aggregate metrics from all edge nodes.

        Fetches ``/internal/stats`` from each configured edge and returns
        per-edge and aggregate statistics.

        Returns:
            A dictionary with ``per_edge`` and ``aggregate`` keys.
        """
        per_edge: dict[str, dict] = {}
        aggregate: dict[str, float | int] = {
            "total_entries": 0,
            "total_size_bytes": 0,
            "hit_count": 0,
            "miss_count": 0,
            "eviction_count": 0,
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            for edge_url in edge_urls:
                try:
                    response = await client.get(f"{edge_url}/internal/stats")
                    if response.status_code == 200:
                        stats = response.json()
                        per_edge[edge_url] = stats

                        # Accumulate aggregate stats
                        for key in aggregate:
                            if key in stats:
                                aggregate[key] += stats[key]
                    else:
                        per_edge[edge_url] = {"error": f"HTTP {response.status_code}"}
                except (httpx.RequestError, Exception) as exc:
                    logger.warning("Failed to fetch stats from %s: %s", edge_url, exc)
                    per_edge[edge_url] = {"error": str(exc)}

        total = aggregate["hit_count"] + aggregate["miss_count"]
        aggregate["hit_ratio"] = (
            aggregate["hit_count"] / total if total > 0 else 0.0
        )

        return {"per_edge": per_edge, "aggregate": aggregate}

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Serve a simple HTML dashboard for metrics visualisation.

        Returns:
            An HTML page with embedded JavaScript that polls the
            ``/metrics`` endpoint and renders charts.
        """
        return _DASHBOARD_HTML

    return router


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CDN Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0f1117; color: #e0e0e0; padding: 24px; }
  h1 { color: #fff; margin-bottom: 24px; }
  h2 { color: #aaa; margin-bottom: 12px; font-size: 1rem; text-transform: uppercase; letter-spacing: .5px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 32px; }
  .card { background: #1a1d27; border-radius: 8px; padding: 20px; }
  .card .label { font-size: .85rem; color: #888; margin-bottom: 4px; }
  .card .value { font-size: 1.8rem; font-weight: 600; color: #fff; }
  .card .value.hit  { color: #4caf50; }
  .card .value.miss { color: #f44336; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
  th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid #2a2d37; }
  th { color: #888; font-weight: 500; font-size: .85rem; text-transform: uppercase; }
  td { color: #e0e0e0; }
  .bar-bg { background: #2a2d37; border-radius: 4px; height: 8px; }
  .bar-fill { background: #4caf50; border-radius: 4px; height: 8px; transition: width .3s; }
  #status { color: #666; font-size: .8rem; margin-top: 16px; }
</style>
</head>
<body>
<h1>CDN Dashboard</h1>

<h2>Aggregate</h2>
<div class="grid" id="agg-cards"></div>

<h2>Per Edge</h2>
<table>
  <thead><tr><th>Edge</th><th>Entries</th><th>Size</th><th>Hits</th><th>Misses</th><th>Hit Ratio</th></tr></thead>
  <tbody id="edge-body"></tbody>
</table>

<p id="status">Loading...</p>

<script>
function fmt(n) { return typeof n === 'number' ? n.toLocaleString() : n; }
function fmtBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  return (b/1048576).toFixed(1) + ' MB';
}

async function refresh() {
  try {
    const res = await fetch('/metrics');
    const data = await res.json();
    const agg = data.aggregate || {};
    const perEdge = data.per_edge || {};

    document.getElementById('agg-cards').innerHTML = `
      <div class="card"><div class="label">Total Entries</div><div class="value">${fmt(agg.total_entries||0)}</div></div>
      <div class="card"><div class="label">Total Size</div><div class="value">${fmtBytes(agg.total_size_bytes||0)}</div></div>
      <div class="card"><div class="label">Cache Hits</div><div class="value hit">${fmt(agg.hit_count||0)}</div></div>
      <div class="card"><div class="label">Cache Misses</div><div class="value miss">${fmt(agg.miss_count||0)}</div></div>
      <div class="card"><div class="label">Hit Ratio</div><div class="value">${((agg.hit_ratio||0)*100).toFixed(1)}%</div></div>
      <div class="card"><div class="label">Evictions</div><div class="value">${fmt(agg.eviction_count||0)}</div></div>
    `;

    const tbody = document.getElementById('edge-body');
    tbody.innerHTML = '';
    for (const [url, stats] of Object.entries(perEdge)) {
      if (stats.error) {
        tbody.innerHTML += `<tr><td>${url}</td><td colspan="5" style="color:#f44336">${stats.error}</td></tr>`;
        continue;
      }
      const ratio = stats.hit_ratio || 0;
      tbody.innerHTML += `<tr>
        <td>${url}</td>
        <td>${fmt(stats.total_entries||0)}</td>
        <td>${fmtBytes(stats.total_size_bytes||0)}</td>
        <td>${fmt(stats.hit_count||0)}</td>
        <td>${fmt(stats.miss_count||0)}</td>
        <td><div class="bar-bg"><div class="bar-fill" style="width:${(ratio*100).toFixed(0)}%"></div></div> ${(ratio*100).toFixed(1)}%</td>
      </tr>`;
    }

    document.getElementById('status').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('status').textContent = 'Error: ' + e.message;
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""
