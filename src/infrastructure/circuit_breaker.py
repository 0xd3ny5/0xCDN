"""Circuit breaker for origin server protection.

Implements a three-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN)
that prevents cascading failures when the origin server is down.
"""
from __future__ import annotations

import asyncio
import enum
import time
from typing import Optional


class CircuitState(enum.Enum):
    CLOSED = "closed"       # Normal operation, requests pass through
    OPEN = "open"           # Origin is down, reject requests immediately
    HALF_OPEN = "half_open"  # Testing if origin has recovered


class CircuitOpenError(Exception):
    """Raised when a request is rejected because the circuit is open."""

    def __init__(self, recovery_at: float) -> None:
        self.recovery_at = recovery_at
        remaining = max(0, recovery_at - time.monotonic())
        super().__init__(f"Circuit is open. Recovery in {remaining:.1f}s")


class CircuitBreaker:
    """Circuit breaker with configurable thresholds and recovery.

    Args:
        failure_threshold: Number of consecutive failures to trip the circuit.
        recovery_timeout: Seconds to wait before entering half-open state.
        half_open_max_calls: Max concurrent calls allowed in half-open state.
        success_threshold: Consecutive successes in half-open to close the circuit.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        success_threshold: int = 2,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state, accounting for recovery timeout."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    async def __aenter__(self) -> CircuitBreaker:
        """Acquire permission to make a request through the circuit.

        Raises CircuitOpenError if the circuit is open and the recovery
        timeout has not yet elapsed.
        """
        async with self._lock:
            current = self.state

            if current == CircuitState.OPEN:
                assert self._last_failure_time is not None
                recovery_at = self._last_failure_time + self._recovery_timeout
                raise CircuitOpenError(recovery_at)

            if current == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self._half_open_max_calls:
                    # Too many concurrent probe calls; treat as still open.
                    assert self._last_failure_time is not None
                    recovery_at = self._last_failure_time + self._recovery_timeout
                    raise CircuitOpenError(recovery_at)
                # Transition internal state so subsequent .state reads
                # reflect HALF_OPEN without re-checking the timer.
                if self._state == CircuitState.OPEN:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    self._half_open_calls = 0
                self._half_open_calls += 1

            # CLOSED: always allow.
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: object,
    ) -> bool:
        """Report the result of the request to the circuit breaker.

        If *exc_type* is None the call is treated as a success; otherwise
        it is treated as a failure.  Returns ``False`` so exceptions are
        never suppressed.
        """
        if exc_type is None:
            await self.record_success()
        else:
            await self.record_failure()
        return False

    async def record_success(self) -> None:
        """Record a successful request."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls = max(0, self._half_open_calls - 1)
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    # Enough consecutive successes; close the circuit.
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._half_open_calls = 0
                    self._last_failure_time = None
            elif self._state == CircuitState.CLOSED:
                # Any success in CLOSED resets the consecutive failure counter.
                self._failure_count = 0

    async def record_failure(self) -> None:
        """Record a failed request."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN trips immediately back to OPEN.
                self._state = CircuitState.OPEN
                self._last_failure_time = time.monotonic()
                self._success_count = 0
                self._half_open_calls = 0
            else:
                # CLOSED (or already OPEN, which is a no-op duplicate).
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                if self._failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._success_count = 0
                    self._half_open_calls = 0

    async def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            self._half_open_calls = 0

    def stats(self) -> dict:
        """Return circuit breaker state and counters."""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self._failure_threshold,
            "recovery_timeout": self._recovery_timeout,
            "half_open_max_calls": self._half_open_max_calls,
            "success_threshold": self._success_threshold,
            "half_open_calls": self._half_open_calls,
            "last_failure_time": self._last_failure_time,
        }
