"""HTTP client for fetching content from the origin server."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import httpx

from domain.entities import OriginResponse


class OriginFetchError(Exception):
    """Raised when all retry attempts to fetch from the origin are exhausted."""

    def __init__(self, path: str, last_status: int | None = None, message: str = "") -> None:
        self.path = path
        self.last_status = last_status
        super().__init__(message or f"Failed to fetch '{path}' from origin after all retries")


class OriginClient(ABC):
    """Abstract base class for origin server clients."""

    @abstractmethod
    async def fetch(
        self, path: str, headers: dict[str, str] | None = None
    ) -> OriginResponse: ...


class HttpOriginClient(OriginClient):
    """Concrete origin client that communicates with the origin over HTTP.

    Parameters:
        origin_url: Base URL of the origin server (e.g. ``"http://localhost:8000"``).
        timeout: Request timeout in seconds.
        max_retries: Maximum number of fetch attempts before raising
            :class:`OriginFetchError`.
    """

    _BACKOFF_DELAYS = [0.5, 1.0, 2.0]

    def __init__(
        self,
        origin_url: str,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._origin_url = origin_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)

    async def fetch(
        self, path: str, headers: dict[str, str] | None = None
    ) -> OriginResponse:
        """Fetch a file from the origin server.

        Sends a GET request to ``{origin_url}/files/{path}`` with optional
        headers (e.g. ``If-None-Match``, ``Range``).  Retries on 5xx responses
        or connection errors using exponential back-off.

        Returns:
            An :class:`OriginResponse` containing the body, status code,
            response headers and ETag (if present).

        Raises:
            OriginFetchError: If all retry attempts are exhausted.
        """
        url = f"{self._origin_url}/files/{path.lstrip('/')}"
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

                # 5xx — fall through to retry
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc

            # Wait before next attempt (skip sleep after the final attempt)
            if attempt < self._max_retries - 1:
                delay = self._BACKOFF_DELAYS[min(attempt, len(self._BACKOFF_DELAYS) - 1)]
                await asyncio.sleep(delay)

        raise OriginFetchError(
            path=path,
            last_status=last_status,
            message=(
                f"Failed to fetch '{path}' from origin after {self._max_retries} attempts"
                + (f" (last status: {last_status})" if last_status else "")
                + (f" (last error: {last_error})" if last_error else "")
            ),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
