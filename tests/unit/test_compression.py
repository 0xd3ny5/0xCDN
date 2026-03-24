"""Unit tests for the compression module."""

from __future__ import annotations

import zlib

import pytest

from infrastructure.compression import (
    _parse_accept_encoding,
    compress_response,
    is_compressible,
)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_gzip_compression() -> None:
    """Gzip-compressed output can be decompressed back to the original."""
    original = b"x" * 2048  # well above the 1 KB threshold

    compressed, encoding = compress_response(
        original, "gzip", content_type="text/html"
    )
    assert encoding == "gzip"
    assert compressed != original
    decompressed = zlib.decompress(compressed, wbits=31)
    assert decompressed == original


def test_no_compression_small_content() -> None:
    """Content smaller than 1 KB is returned uncompressed."""
    small = b"tiny"
    result, encoding = compress_response(
        small, "gzip", content_type="text/plain"
    )

    assert encoding is None
    assert result == small


def test_no_compression_binary() -> None:
    """Binary content types (images) are not compressed."""
    data = b"x" * 2048
    result, encoding = compress_response(
        data, "gzip", content_type="image/png"
    )

    assert encoding is None
    assert result == data


def test_accept_encoding_parsing() -> None:
    """Various Accept-Encoding header formats are parsed correctly."""
    # Simple
    assert _parse_accept_encoding("gzip") == {"gzip"}

    # Multiple encodings
    assert _parse_accept_encoding("gzip, deflate, br") == {"gzip", "deflate", "br"}

    # With quality factors
    assert _parse_accept_encoding("gzip;q=1.0, deflate;q=0.5") == {"gzip", "deflate"}

    # Empty / whitespace
    assert _parse_accept_encoding("") == set()
    assert _parse_accept_encoding("  ") == set()

    # Wildcard
    assert _parse_accept_encoding("*") == {"*"}


def test_is_compressible_text_types() -> None:
    """Text-like MIME types are compressible."""
    assert is_compressible("text/html") is True
    assert is_compressible("text/css") is True
    assert is_compressible("application/json") is True
    assert is_compressible("application/javascript") is True


def test_is_compressible_binary_types() -> None:
    """Binary MIME types are not compressible."""
    assert is_compressible("image/png") is False
    assert is_compressible("image/jpeg") is False
    assert is_compressible("application/zip") is False
    assert is_compressible("video/mp4") is False


def test_deflate_encoding() -> None:
    """When only deflate is accepted, deflate encoding is used."""
    data = b"y" * 2048
    compressed, encoding = compress_response(
        data, "deflate", content_type="text/plain"
    )
    assert encoding == "deflate"
    decompressed = zlib.decompress(compressed)
    assert decompressed == data


def test_no_matching_encoding() -> None:
    """When the client accepts no supported encoding, content is uncompressed."""
    data = b"z" * 2048
    result, encoding = compress_response(
        data, "identity", content_type="text/plain"
    )
    assert encoding is None
    assert result == data
