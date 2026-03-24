"""Origin server routes.

Serves files from a local assets directory with support for conditional
requests (ETag / If-None-Match), Range requests (HTTP 206), and proper
Content-Type detection.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response


def create_router(assets_dir: str) -> APIRouter:
    """Create the origin server router.

    Args:
        assets_dir: Filesystem path to the directory containing static assets.

    Returns:
        A configured ``APIRouter`` with the ``/files/{path:path}`` endpoint.
    """
    router = APIRouter()
    base_path = Path(assets_dir).resolve()

    @router.get("/files/{path:path}")
    async def serve_file(
        path: str,
        request: Request,
        if_none_match: Optional[str] = Header(None, alias="if-none-match"),
        range_header: Optional[str] = Header(None, alias="range"),
    ) -> Response:
        """Serve a file from the assets directory.

        Supports:
        - ETag-based conditional requests (304 Not Modified).
        - Byte-range requests (206 Partial Content).
        - Content-Type detection via file extension.
        """
        file_path = (base_path / path).resolve()

        # Prevent directory traversal outside assets_dir
        if not str(file_path).startswith(str(base_path)):
            return Response(status_code=403, content="Forbidden")

        if not file_path.is_file():
            return Response(status_code=404, content="Not Found")

        content = file_path.read_bytes()
        etag = hashlib.md5(content).hexdigest()

        # Conditional request: If-None-Match
        if if_none_match and if_none_match.strip('"') == etag:
            return Response(status_code=304, headers={"ETag": f'"{etag}"'})

        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        # Range request
        if range_header:
            return _handle_range(content, range_header, etag, content_type)

        # Full response
        return Response(
            content=content,
            status_code=200,
            media_type=content_type,
            headers={
                "ETag": f'"{etag}"',
                "Content-Length": str(len(content)),
                "Accept-Ranges": "bytes",
            },
        )

    return router


def _handle_range(
    content: bytes,
    range_header: str,
    etag: str,
    content_type: str,
) -> Response:
    """Parse a Range header and return a 206 Partial Content response.

    Args:
        content: Full file content bytes.
        range_header: Raw ``Range`` header value.
        etag: Computed ETag for the file.
        content_type: MIME type of the file.

    Returns:
        A ``Response`` with status 206 and appropriate Content-Range header,
        or 416 if the range is not satisfiable.
    """
    from domain.value_objects import ByteRange

    total = len(content)

    try:
        ranges = ByteRange.from_header(range_header)
    except ValueError:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total}"},
        )

    if not ranges:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total}"},
        )

    byte_range = ranges[0]

    # Resolve start/end
    if byte_range.start is None and byte_range.end is not None:
        start = max(total - byte_range.end, 0)
        end = total - 1
    elif byte_range.end is None and byte_range.start is not None:
        start = byte_range.start
        end = total - 1
    else:
        start = byte_range.start if byte_range.start is not None else 0
        end = byte_range.end if byte_range.end is not None else total - 1

    end = min(end, total - 1)

    if start > end or start >= total:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total}"},
        )

    partial = content[start : end + 1]

    return Response(
        content=partial,
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(len(partial)),
            "Accept-Ranges": "bytes",
            "ETag": f'"{etag}"',
        },
    )
