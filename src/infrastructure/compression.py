"""Response compression utilities.

Supports gzip, deflate, and (optionally) Brotli.  Only stdlib ``zlib`` is
required; the ``brotli`` package is used when available but is not mandatory.
"""

from __future__ import annotations

import zlib

try:
    import brotli as _brotli  # type: ignore[import-untyped]

    _HAS_BROTLI = True
except ImportError:
    _brotli = None
    _HAS_BROTLI = False

# Content types that should never be compressed (already compressed or binary).
_SKIP_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/svg+xml",
        "video/mp4",
        "video/webm",
        "video/ogg",
        "audio/mpeg",
        "audio/ogg",
        "application/zip",
        "application/gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
        "application/zstd",
        "application/wasm",
    }
)

# Content-type prefixes / patterns considered compressible.
_COMPRESSIBLE_TYPES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/ld+json",
    "application/manifest+json",
    "application/vnd.api+json",
)

# Minimum content size (bytes) worth compressing.
_MIN_COMPRESS_SIZE = 1024


def is_compressible(content_type: str) -> bool:
    """Return ``True`` if *content_type* is a text-like MIME type that benefits
    from compression.

    Binary and already-compressed types (images, video, archives) return
    ``False``.
    """
    # Normalise: strip parameters like "; charset=utf-8"
    ct = content_type.split(";", 1)[0].strip().lower()

    if ct in _SKIP_CONTENT_TYPES:
        return False

    return any(ct.startswith(prefix) for prefix in _COMPRESSIBLE_TYPES)


def _parse_accept_encoding(accept_encoding: str) -> set[str]:
    """Extract the set of accepted encoding names from an Accept-Encoding
    header value."""
    encodings: set[str] = set()
    for part in accept_encoding.split(","):
        part = part.strip()
        if not part:
            continue
        # "gzip;q=1.0" -> "gzip"
        name = part.split(";", 1)[0].strip().lower()
        if name:
            encodings.add(name)
    return encodings


def compress_response(
    content: bytes,
    accept_encoding: str,
    content_type: str = "application/octet-stream",
) -> tuple[bytes, str | None]:
    """Compress *content* using the best mutually-supported encoding.

    The caller supplies the raw ``Accept-Encoding`` header value.  Encoding
    preference order is **brotli > gzip > deflate**.

    Returns:
        A ``(data, encoding_name)`` tuple.  *encoding_name* is ``None`` when
        no compression was applied (content too small, incompressible type, or
        no shared encoding).
    """
    # Skip small payloads or non-compressible content types.
    if len(content) < _MIN_COMPRESS_SIZE:
        return content, None

    if not is_compressible(content_type):
        return content, None

    accepted = _parse_accept_encoding(accept_encoding)

    # Prefer brotli > gzip > deflate
    if _HAS_BROTLI and "br" in accepted:
        compressed = _brotli.compress(content)  # type: ignore[union-attr]
        return compressed, "br"

    if "gzip" in accepted:
        # Use compressobj with wbits=31 for gzip-wrapped stream.
        c = zlib.compressobj(level=6, wbits=31)
        compressed = c.compress(content) + c.flush()
        return compressed, "gzip"

    if "deflate" in accepted:
        compressed = zlib.compress(content)
        return compressed, "deflate"

    return content, None
