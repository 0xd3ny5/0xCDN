"""Unit tests for Cache-Control header parsing."""

from __future__ import annotations

import pytest

from infrastructure.cache_control import CacheDirectives, parse_cache_control


class TestParseCacheControl:
    """Tests for the parse_cache_control function."""

    def test_empty_header(self) -> None:
        d = parse_cache_control("")
        assert d == CacheDirectives()

    def test_max_age(self) -> None:
        d = parse_cache_control("max-age=3600")
        assert d.max_age == 3600
        assert d.is_cacheable is True

    def test_s_maxage(self) -> None:
        d = parse_cache_control("s-maxage=600")
        assert d.s_maxage == 600

    def test_s_maxage_overrides_max_age(self) -> None:
        d = parse_cache_control("max-age=60, s-maxage=300")
        assert d.effective_ttl(default_ttl=3600) == 300.0

    def test_no_store(self) -> None:
        d = parse_cache_control("no-store")
        assert d.no_store is True
        assert d.is_cacheable is False

    def test_no_cache(self) -> None:
        d = parse_cache_control("no-cache")
        assert d.no_cache is True
        assert d.effective_ttl(default_ttl=3600) == 0.0

    def test_private(self) -> None:
        d = parse_cache_control("private, max-age=120")
        assert d.private is True
        assert d.is_cacheable is False
        assert d.max_age == 120

    def test_must_revalidate(self) -> None:
        d = parse_cache_control("max-age=0, must-revalidate")
        assert d.must_revalidate is True
        assert d.max_age == 0

    def test_stale_while_revalidate(self) -> None:
        d = parse_cache_control("max-age=300, stale-while-revalidate=60")
        assert d.stale_while_revalidate == 60
        assert d.effective_stale_while_revalidate() == 60.0

    def test_stale_if_error(self) -> None:
        d = parse_cache_control("max-age=300, stale-if-error=120")
        assert d.stale_if_error == 120

    def test_combined_directives(self) -> None:
        d = parse_cache_control(
            "public, max-age=86400, s-maxage=3600, stale-while-revalidate=600"
        )
        assert d.max_age == 86400
        assert d.s_maxage == 3600
        assert d.stale_while_revalidate == 600
        assert d.is_cacheable is True
        assert d.effective_ttl(default_ttl=60) == 3600.0

    def test_quoted_values(self) -> None:
        d = parse_cache_control('max-age="120"')
        assert d.max_age == 120

    def test_whitespace_handling(self) -> None:
        d = parse_cache_control("  max-age = 60 ,  no-cache  ")
        assert d.max_age == 60
        assert d.no_cache is True

    def test_unknown_directives_ignored(self) -> None:
        d = parse_cache_control("public, max-age=300, proxy-revalidate")
        assert d.max_age == 300

    def test_effective_ttl_default(self) -> None:
        d = parse_cache_control("")
        assert d.effective_ttl(default_ttl=7200) == 7200.0

    def test_effective_swr_absent(self) -> None:
        d = parse_cache_control("max-age=300")
        assert d.effective_stale_while_revalidate() == 0.0
