"""Unit tests for the CircuitBreaker."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from infrastructure.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


async def test_initial_state_is_closed() -> None:
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED


async def test_stays_closed_under_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == CircuitState.CLOSED


async def test_opens_at_failure_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=2)
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN


async def test_success_resets_failure_count() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    await cb.record_failure()
    await cb.record_failure()
    await cb.record_success()
    # Counter reset; two more failures needed to trip
    await cb.record_failure()
    assert cb.state == CircuitState.CLOSED


async def test_open_raises_circuit_open_error() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=300.0)
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        async with cb:
            pass


async def test_transitions_to_half_open_after_timeout() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN

    await asyncio.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN


async def test_half_open_success_closes_circuit() -> None:
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0.05,
        half_open_max_calls=2,
        success_threshold=2,
    )
    await cb.record_failure()
    await asyncio.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN

    # Use context manager to properly enter half-open state
    async with cb:
        pass  # success 1
    assert cb.state == CircuitState.HALF_OPEN

    async with cb:
        pass  # success 2
    assert cb.state == CircuitState.CLOSED


async def test_half_open_failure_reopens_circuit() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
    await cb.record_failure()
    await asyncio.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN

    await cb.record_failure()
    assert cb.state == CircuitState.OPEN


async def test_context_manager_records_success() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    await cb.record_failure()
    await cb.record_failure()  # 2 failures, threshold=3
    assert cb.state == CircuitState.CLOSED

    async with cb:
        pass  # success

    assert cb._failure_count == 0  # reset on success


async def test_context_manager_records_failure() -> None:
    cb = CircuitBreaker(failure_threshold=2)
    with pytest.raises(ValueError):
        async with cb:
            raise ValueError("boom")
    assert cb._failure_count == 1


async def test_manual_reset() -> None:
    cb = CircuitBreaker(failure_threshold=1)
    await cb.record_failure()
    assert cb.state == CircuitState.OPEN

    await cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


async def test_stats() -> None:
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
    await cb.record_failure()
    s = cb.stats()
    assert s["state"] == "closed"
    assert s["failure_count"] == 1
    assert s["failure_threshold"] == 3
