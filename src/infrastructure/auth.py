"""Signed-URL and token authentication utilities.

All cryptographic operations use stdlib ``hmac`` / ``hashlib`` so no
third-party dependencies are required.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qs, urlparse


def generate_token(path: str, secret_key: str, expires_at: int) -> str:
    """Create an HMAC-SHA256 token for the given *path* and *expires_at* Unix
    timestamp.

    The token is returned as a URL-safe base64-encoded string (no padding).
    """
    message = f"{path}:{expires_at}".encode()
    digest = hmac.new(secret_key.encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def validate_token(
    path: str, token: str, expires_at: int, secret_key: str
) -> bool:
    """Validate a signed-URL token.

    Returns ``True`` only when the token matches the expected HMAC **and** the
    expiration time has not yet passed.
    """
    if expires_at < int(time.time()):
        return False

    expected = generate_token(path, secret_key, expires_at)
    return hmac.compare_digest(expected, token)


def parse_signed_url(url: str) -> tuple[str, str, int] | None:
    """Extract ``(path, token, expires)`` from a signed URL.

    The URL is expected to contain ``token`` and ``expires`` query parameters.
    Returns ``None`` if either parameter is missing or *expires* is not a valid
    integer.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    token_values = params.get("token")
    expires_values = params.get("expires")

    if not token_values or not expires_values:
        return None

    try:
        expires = int(expires_values[0])
    except (ValueError, IndexError):
        return None

    return parsed.path, token_values[0], expires
