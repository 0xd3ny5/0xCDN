from __future__ import annotations

__all__: tuple[str, ...] = (
    "CacheKey",
    "ByteRange",
)

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode


class CacheKey:
    """Immutable, hashable key used to identify a cached resource.

    A cache key is built from the HTTP method, request path, and sorted
    query parameters so that semantically identical requests always map
    to the same key regardless of parameter ordering.
    """

    __slots__ = ("_method", "_path", "_query", "_vary_hash", "_key")

    def __init__(self, method: str, path: str, query: str, vary_hash: str = "") -> None:
        self._method = method.upper()
        self._path = path
        self._query = query
        self._vary_hash = vary_hash
        base = f"{self._method}:{self._path}?{self._query}" if self._query else f"{self._method}:{self._path}"
        self._key = f"{base}|vary={self._vary_hash}" if self._vary_hash else base

    @classmethod
    def from_request(
        cls,
        method: str,
        path: str,
        query_params: Optional[dict[str, str]] = None,
        vary_headers: Optional[dict[str, str]] = None,
    ) -> CacheKey:
        """Build cache key including Vary header values.

        Args:
            method: HTTP method (e.g. ``"GET"``).
            path: URL path (e.g. ``"/images/logo.png"``).
            query_params: Mapping of query-string parameter names to values.
                Parameters are sorted by key to ensure deterministic keys.
            vary_headers: A dict of ``{header_name: header_value}`` for headers
                listed in the ``Vary`` response header.  For example, if the
                origin responded with ``Vary: Accept-Encoding`` and the request
                had ``Accept-Encoding: gzip``, then
                ``vary_headers = {"accept-encoding": "gzip"}``.

        Returns:
            A new ``CacheKey`` instance.
        """
        if query_params:
            sorted_params = sorted(query_params.items())
            query = urlencode(sorted_params)
        else:
            query = ""

        vary_hash = ""
        if vary_headers:
            # Sort and hash vary header values for deterministic keys
            import hashlib
            sorted_vary = sorted(vary_headers.items())
            vary_str = "&".join(f"{k}={v}" for k, v in sorted_vary)
            vary_hash = hashlib.md5(vary_str.encode()).hexdigest()[:8]

        return cls(method, path, query, vary_hash)

    @property
    def method(self) -> str:
        """The HTTP method component of the key."""
        return self._method

    @property
    def path(self) -> str:
        """The path component of the key."""
        return self._path

    def __str__(self) -> str:
        return self._key

    def __repr__(self) -> str:
        return f"CacheKey({self._key!r})"

    def __hash__(self) -> int:
        return hash(self._key)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CacheKey):
            return self._key == other._key
        return NotImplemented


# RFC 7233 byte-range helpers
_RANGE_SPEC_RE = re.compile(r"^\s*bytes\s*=\s*(.+)$", re.IGNORECASE)
_RANGE_PART_RE = re.compile(r"^\s*(\d*)\s*-\s*(\d*)\s*$")


@dataclass(frozen=True)
class ByteRange:
    """Represents a single byte range as defined by RFC 7233.

    Either ``start`` or ``end`` may be ``None`` to represent suffix/prefix
    ranges (e.g. ``bytes=-500`` or ``bytes=9500-``).

    Attributes:
        start: First byte position (inclusive), or None for a suffix range.
        end: Last byte position (inclusive), or None for an open-ended range.
    """

    start: Optional[int] = None
    end: Optional[int] = None

    def to_header(self) -> str:
        """Format this range as a ``Range`` header value.

        Returns:
            A string such as ``"bytes=0-499"`` or ``"bytes=-500"``.
        """
        start_str = str(self.start) if self.start is not None else ""
        end_str = str(self.end) if self.end is not None else ""
        return f"bytes={start_str}-{end_str}"

    def content_range(self, total: int) -> str:
        """Build a ``Content-Range`` header value for a given total size.

        The method resolves suffix ranges and open-ended ranges against
        *total* so the returned header always contains concrete positions.

        Args:
            total: Total size of the complete resource in bytes.

        Returns:
            A string such as ``"bytes 0-499/1000"``.
        """
        if self.start is None and self.end is not None:
            # Suffix range: last N bytes
            resolved_start = max(total - self.end, 0)
            resolved_end = total - 1
        elif self.end is None and self.start is not None:
            # Open-ended range: from start to the end of the resource
            resolved_start = self.start
            resolved_end = total - 1
        else:
            resolved_start = self.start if self.start is not None else 0
            resolved_end = self.end if self.end is not None else total - 1
        return f"bytes {resolved_start}-{resolved_end}/{total}"

    @classmethod
    def from_header(cls, header: str) -> list[ByteRange]:
        """Parse an RFC 7233 ``Range`` header into a list of ``ByteRange`` objects.

        Supports multiple comma-separated range specifications within a
        single header value (e.g. ``"bytes=0-499, 1000-1499"``).

        Args:
            header: The raw ``Range`` header string.

        Returns:
            A list of parsed ``ByteRange`` instances.

        Raises:
            ValueError: If the header cannot be parsed as a valid byte range.
        """
        match = _RANGE_SPEC_RE.match(header)
        if not match:
            raise ValueError(f"Invalid Range header: {header!r}")

        range_set = match.group(1)
        ranges: list[ByteRange] = []

        for part in range_set.split(","):
            part_match = _RANGE_PART_RE.match(part)
            if not part_match:
                raise ValueError(f"Invalid range specification: {part.strip()!r}")

            start_str, end_str = part_match.group(1), part_match.group(2)

            if not start_str and not end_str:
                raise ValueError(f"Invalid range specification: {part.strip()!r}")

            start = int(start_str) if start_str else None
            end = int(end_str) if end_str else None
            ranges.append(cls(start=start, end=end))

        return ranges
