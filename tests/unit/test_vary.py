"""Unit tests for Vary header support in cache keys."""

from __future__ import annotations

from domain.value_objects import CacheKey


def test_same_path_different_vary_produces_different_keys() -> None:
    key_gzip = CacheKey.from_request(
        "GET", "/style.css", vary_headers={"accept-encoding": "gzip"}
    )
    key_br = CacheKey.from_request(
        "GET", "/style.css", vary_headers={"accept-encoding": "br"}
    )
    assert str(key_gzip) != str(key_br)


def test_same_vary_produces_same_key() -> None:
    key1 = CacheKey.from_request(
        "GET", "/style.css", vary_headers={"accept-encoding": "gzip"}
    )
    key2 = CacheKey.from_request(
        "GET", "/style.css", vary_headers={"accept-encoding": "gzip"}
    )
    assert str(key1) == str(key2)
    assert key1 == key2
    assert hash(key1) == hash(key2)


def test_no_vary_omits_vary_segment() -> None:
    key = CacheKey.from_request("GET", "/index.html")
    assert "|vary=" not in str(key)


def test_vary_present_includes_vary_segment() -> None:
    key = CacheKey.from_request(
        "GET", "/index.html", vary_headers={"accept-encoding": "gzip"}
    )
    assert "|vary=" in str(key)


def test_vary_header_order_does_not_matter() -> None:
    key1 = CacheKey.from_request(
        "GET", "/api",
        vary_headers={"accept-encoding": "gzip", "accept-language": "en"},
    )
    key2 = CacheKey.from_request(
        "GET", "/api",
        vary_headers={"accept-language": "en", "accept-encoding": "gzip"},
    )
    assert str(key1) == str(key2)


def test_empty_vary_dict_same_as_no_vary() -> None:
    key_none = CacheKey.from_request("GET", "/file.txt")
    key_empty = CacheKey.from_request("GET", "/file.txt", vary_headers={})
    assert str(key_none) == str(key_empty)
