"""HTTP client for fetching content through the origin shield layer.

The origin shield is an intermediate cache (typically another edge node with a
larger cache) that sits between edge nodes and the true origin server.  This
client implements the same :class:`OriginClient` interface so it can be used
as a transparent drop-in replacement.
"""

from __future__ import annotations

import asyncio

import httpx

from domain.entities import OriginResponse
from infrastructure.http.origin_client import OriginClient, OriginFetchError


class ShieldClient(OriginClient):
    """Client that routes origin fetches through a shield node.

    Parameters:
        shield_url: Base URL of the shield node (e.g. ``"http://shield:8001"``).
        timeout: Request timeout in seconds.
        max_retries: Maximum number of fetch attempts before raising
            :class:`OriginFetchError`.
    """

    _BACKOFF_DELAYS = [0.5, 1.0, 2.0]

    def __init__(
        self,
        shield_url: str,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._shield_url = shield_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)

    async def fetch(
        self, path: str, headers: dict[str, str] | None = None
    ) -> OriginResponse:
        """Fetch a file through the shield node.

        Sends a GET request to ``{shield_url}/files/{path}`` with optional
        headers.  Retry behaviour mirrors :class:`HttpOriginClient`.

        Returns:
            An :class:`OriginResponse` with body, status code, headers and
            ETag.

        Raises:
            OriginFetchError: If all retry attempts are exhausted.
        """
        url = f"{self._shield_url}/files/{path.lstrip('/')}"
        last_status: int | None = None
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                response = await self._client.get(url, headers=headers)
                last_status = response.status_code

                if response.status_code < 500:
                    response_headers = dict(response.headers)
                    etag = response_headers.get("etag")
                    return OriginResponse(
                        content=response.content,
                        status_code=response.status_code,
                        headers=response_headers,
                        etag=etag,
                    )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc

            if attempt < self._max_retries - 1:
                delay = self._BACKOFF_DELAYS[min(attempt, len(self._BACKOFF_DELAYS) - 1)]
                await asyncio.sleep(delay)

        raise OriginFetchError(
            path=path,
            last_status=last_status,
            message=(
                f"Failed to fetch '{path}' from shield after {self._max_retries} attempts"
                + (f" (last status: {last_status})" if last_status else "")
                + (f" (last error: {last_error})" if last_error else "")
            ),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
