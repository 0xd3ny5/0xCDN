"""Structured JSON logger for CDN components."""

from __future__ import annotations

import json
import logging
from typing import Any


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        # Merge any extra fields attached by CDNLogger.
        extra: dict[str, Any] = getattr(record, "extra_fields", {})
        if extra:
            log_entry.update(extra)
        return json.dumps(log_entry, default=str)


class CDNLogger:
    """Thin wrapper around stdlib :mod:`logging` that emits structured JSON.

    Parameters:
        name: Logger name (typically the component identifier).
        level: Minimum log level as a string (e.g. ``"INFO"``, ``"DEBUG"``).
    """

    def __init__(self, name: str, level: str = "INFO") -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))

        # Avoid adding duplicate handlers when a logger with the same name is
        # instantiated more than once.
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JsonFormatter())
            self._logger.addHandler(handler)

    def request(
        self,
        edge_id: str,
        path: str,
        cache_hit: bool,
        response_time: float,
        status_code: int,
        bytes_sent: int,
    ) -> None:
        """Log an edge request."""
        self._log(
            logging.INFO,
            "request",
            edge_id=edge_id,
            path=path,
            cache_hit=cache_hit,
            response_time_ms=round(response_time * 1000, 2),
            status_code=status_code,
            bytes_sent=bytes_sent,
        )

    def origin_fetch(
        self,
        edge_id: str,
        path: str,
        fetch_time: float,
        status_code: int,
    ) -> None:
        """Log an origin fetch performed by an edge node."""
        self._log(
            logging.INFO,
            "origin_fetch",
            edge_id=edge_id,
            path=path,
            fetch_time_ms=round(fetch_time * 1000, 2),
            status_code=status_code,
        )

    def error(self, message: str, **context: Any) -> None:
        """Log an error with optional context fields."""
        self._log(logging.ERROR, message, **context)

    def info(self, message: str, **context: Any) -> None:
        """Log an informational message with optional context fields."""
        self._log(logging.INFO, message, **context)

    def _log(self, level: int, message: str, **fields: Any) -> None:
        """Emit a log record with extra structured fields."""
        # Create a LogRecord and attach our extra payload so the
        # JsonFormatter can pick it up.
        extra = {"extra_fields": fields}
        self._logger.log(level, message, extra=extra)
