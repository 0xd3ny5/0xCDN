"""Unit tests for the auth module (signed URLs and token validation)."""

from __future__ import annotations

import time

from infrastructure.auth import (
    generate_token,
    parse_signed_url,
    validate_token,
)

SECRET = "test-secret-key"


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_generate_and_validate_token() -> None:
    """A token generated for a path validates correctly before expiry."""
    path = "/files/image.png"
    expires_at = int(time.time()) + 3600

    token = generate_token(path, SECRET, expires_at)
    assert isinstance(token, str)
    assert len(token) > 0

    assert validate_token(path, token, expires_at, SECRET) is True


def test_expired_token_rejected() -> None:
    """A token whose expiry timestamp is in the past fails validation."""
    path = "/files/doc.pdf"
    expires_at = int(time.time()) - 10  # already expired

    token = generate_token(path, SECRET, expires_at)
    assert validate_token(path, token, expires_at, SECRET) is False


def test_wrong_path_rejected() -> None:
    """A token generated for path /a fails validation against path /b."""
    expires_at = int(time.time()) + 3600

    token = generate_token("/files/a.txt", SECRET, expires_at)
    assert validate_token("/files/b.txt", token, expires_at, SECRET) is False


def test_wrong_secret_rejected() -> None:
    """A token validated with a different secret is rejected."""
    path = "/files/secret.dat"
    expires_at = int(time.time()) + 3600

    token = generate_token(path, SECRET, expires_at)
    assert validate_token(path, token, expires_at, "wrong-secret") is False


def test_parse_signed_url() -> None:
    """parse_signed_url correctly extracts path, token, and expires."""
    url = "https://cdn.example.com/files/img.png?token=abc123&expires=1700000000"
    result = parse_signed_url(url)

    assert result is not None
    path, token, expires = result
    assert path == "/files/img.png"
    assert token == "abc123"
    assert expires == 1700000000


def test_parse_signed_url_missing_params() -> None:
    """parse_signed_url returns None when required params are missing."""
    assert parse_signed_url("https://cdn.example.com/files/img.png") is None
    assert parse_signed_url("https://cdn.example.com/files/img.png?token=abc") is None
    assert parse_signed_url("https://cdn.example.com/files/img.png?expires=123") is None


def test_parse_signed_url_invalid_expires() -> None:
    """parse_signed_url returns None when expires is not an integer."""
    url = "https://cdn.example.com/files/img.png?token=abc&expires=notanumber"
    assert parse_signed_url(url) is None
