"""Unit tests for ByteRange parsing and Content-Range header generation."""

from __future__ import annotations

import pytest

from domain.value_objects import ByteRange


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_parse_single_range() -> None:
    """'bytes=0-499' is parsed as start=0, end=499."""
    ranges = ByteRange.from_header("bytes=0-499")
    assert len(ranges) == 1

    r = ranges[0]
    assert r.start == 0
    assert r.end == 499


def test_parse_suffix_range() -> None:
    """'bytes=-500' is parsed as a suffix range (start=None, end=500)."""
    ranges = ByteRange.from_header("bytes=-500")
    assert len(ranges) == 1

    r = ranges[0]
    assert r.start is None
    assert r.end == 500


def test_parse_open_ended_range() -> None:
    """'bytes=9500-' is parsed as start=9500, end=None."""
    ranges = ByteRange.from_header("bytes=9500-")
    assert len(ranges) == 1

    r = ranges[0]
    assert r.start == 9500
    assert r.end is None


def test_parse_multiple_ranges() -> None:
    """Multiple comma-separated ranges are all parsed."""
    ranges = ByteRange.from_header("bytes=0-499, 1000-1499")
    assert len(ranges) == 2
    assert ranges[0].start == 0
    assert ranges[0].end == 499
    assert ranges[1].start == 1000
    assert ranges[1].end == 1499


def test_content_range_header() -> None:
    """content_range() produces the correct 'bytes X-Y/Z' format."""
    r = ByteRange(start=0, end=499)
    assert r.content_range(1000) == "bytes 0-499/1000"


def test_content_range_suffix() -> None:
    """Suffix range content_range resolves against total size."""
    r = ByteRange(start=None, end=500)
    header = r.content_range(1000)
    assert header == "bytes 500-999/1000"


def test_content_range_open_ended() -> None:
    """Open-ended range content_range resolves end to total - 1."""
    r = ByteRange(start=9500, end=None)
    header = r.content_range(10000)
    assert header == "bytes 9500-9999/10000"


def test_to_header() -> None:
    """to_header() round-trips correctly for different range types."""
    assert ByteRange(start=0, end=499).to_header() == "bytes=0-499"
    assert ByteRange(start=None, end=500).to_header() == "bytes=-500"
    assert ByteRange(start=9500, end=None).to_header() == "bytes=9500-"


def test_invalid_range() -> None:
    """Malformed Range headers raise ValueError."""
    with pytest.raises(ValueError):
        ByteRange.from_header("invalid-header")

    with pytest.raises(ValueError):
        ByteRange.from_header("bytes=")

    with pytest.raises(ValueError):
        ByteRange.from_header("items=0-10")
