"""Unified CDN entrypoint.

Reads the ``CDN_ROLE`` environment variable to determine which application
to start, then launches it with uvicorn.

Supported roles:
    - ``origin``     -- origin file server
    - ``edge``       -- edge caching proxy
    - ``router``     -- request router / load balancer
    - ``management`` -- management / admin API
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Resolve the requested role and start the corresponding app."""
    import uvicorn

    role = os.environ.get("CDN_ROLE", "edge").lower().strip()
    host = os.environ.get("CDN_HOST", "0.0.0.0")
    port = int(os.environ.get("CDN_PORT", "8000"))

    factories = {
        "origin": _start_origin,
        "edge": _start_edge,
        "router": _start_router,
        "management": _start_management,
    }

    factory = factories.get(role)
    if factory is None:
        print(
            f"ERROR: Unknown CDN_ROLE '{role}'. "
            f"Must be one of: {', '.join(factories)}",
            file=sys.stderr,
        )
        sys.exit(1)

    app = factory()
    uvicorn.run(app, host=host, port=port)


def _start_origin():
    from presentation.origin.app import create_origin_app

    return create_origin_app()


def _start_edge():
    from presentation.edge.app import create_edge_app

    return create_edge_app()


def _start_router():
    from presentation.router.app import create_router_app

    return create_router_app()


def _start_management():
    from presentation.management.app import create_management_app

    return create_management_app()


if __name__ == "__main__":
    main()
