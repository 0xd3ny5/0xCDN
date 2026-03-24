"""Cache-Control header parser.

Parses Cache-Control response directives per RFC 7234 to determine
cacheability and TTL for origin responses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CacheDirectives:
    """Parsed Cache-Control directives from an HTTP response.

    Attributes:
        max_age: Value of max-age directive in seconds, or None if absent.
        s_maxage: Value of s-maxage directive (shared cache TTL), or None.
        no_cache: True if no-cache directive is present.
        no_store: True if no-store directive is present.
        private: True if private directive is present.
        must_revalidate: True if must-revalidate directive is present.
        stale_while_revalidate: Seconds the cache may serve stale content
            while revalidating in the background, or None if absent.
        stale_if_error: Seconds the cache may serve stale content when
            origin returns an error, or None if absent.
    """

    max_age: Optional[int] = None
    s_maxage: Optional[int] = None
    no_cache: bool = False
    no_store: bool = False
    private: bool = False
    must_revalidate: bool = False
    stale_while_revalidate: Optional[int] = None
    stale_if_error: Optional[int] = None

    @property
    def is_cacheable(self) -> bool:
        """Whether the response can be stored by a shared (CDN) cache."""
        return not self.no_store and not self.private

    def effective_ttl(self, default_ttl: float) -> float:
        """Compute the effective TTL for this response.

        Priority: s-maxage > max-age > default_ttl.
        Returns 0 if no-cache (must always revalidate).
        """
        if self.no_cache:
            return 0.0
        if self.s_maxage is not None:
            return float(self.s_maxage)
        if self.max_age is not None:
            return float(self.max_age)
        return default_ttl

    def effective_stale_while_revalidate(self) -> float:
        """Return the stale-while-revalidate window in seconds (0 if absent)."""
        return float(self.stale_while_revalidate) if self.stale_while_revalidate is not None else 0.0


# Regex for extracting an integer value, possibly quoted.
_INT_VALUE_RE = re.compile(r'^"?(\d+)"?$')


def _parse_int(raw: str) -> Optional[int]:
    """Extract a non-negative integer from a directive value.

    Handles optional quoting (e.g. ``max-age="3600"``).
    Returns None if the value is not a valid non-negative integer.
    """
    match = _INT_VALUE_RE.match(raw.strip())
    if match:
        return int(match.group(1))
    return None


def parse_cache_control(header: str) -> CacheDirectives:
    """Parse a Cache-Control header value into CacheDirectives.

    Handles: max-age=N, s-maxage=N, no-cache, no-store, private, public,
    must-revalidate, stale-while-revalidate=N, stale-if-error=N.
    Unknown directives are silently ignored.

    Edge cases handled:
    - Extra whitespace around directives and values.
    - Quoted integer values (e.g. ``max-age="300"``).
    - Empty or whitespace-only header strings.
    - Duplicate directives (last value wins for integer directives).
    """
    max_age: Optional[int] = None
    s_maxage: Optional[int] = None
    no_cache: bool = False
    no_store: bool = False
    private: bool = False
    must_revalidate: bool = False
    stale_while_revalidate: Optional[int] = None
    stale_if_error: Optional[int] = None

    for token in header.split(","):
        token = token.strip()
        if not token:
            continue

        # Split on first '=' to separate key from value.
        if "=" in token:
            key, _, value = token.partition("=")
            key = key.strip().lower()
            value = value.strip()
        else:
            key = token.strip().lower()
            value = ""

        if key == "max-age":
            parsed = _parse_int(value)
            if parsed is not None:
                max_age = parsed
        elif key == "s-maxage":
            parsed = _parse_int(value)
            if parsed is not None:
                s_maxage = parsed
        elif key == "no-cache":
            no_cache = True
        elif key == "no-store":
            no_store = True
        elif key == "private":
            private = True
        elif key == "must-revalidate":
            must_revalidate = True
        elif key == "stale-while-revalidate":
            parsed = _parse_int(value)
            if parsed is not None:
                stale_while_revalidate = parsed
        elif key == "stale-if-error":
            parsed = _parse_int(value)
            if parsed is not None:
                stale_if_error = parsed
        # Unknown directives (e.g. public, proxy-revalidate) are silently ignored.

    return CacheDirectives(
        max_age=max_age,
        s_maxage=s_maxage,
        no_cache=no_cache,
        no_store=no_store,
        private=private,
        must_revalidate=must_revalidate,
        stale_while_revalidate=stale_while_revalidate,
        stale_if_error=stale_if_error,
    )
