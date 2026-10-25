"""MASR's routing circuit breaker must be able to open, and the shared
CircuitBreaker must not wedge in HALF_OPEN."""

from __future__ import annotations

import asyncio

import pytest

from src.ai_brain.router.masr import MASRouter
from src.reliability.retry_strategies import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)


async def _ok() -> int:
    return 1


async def _boom() -> None:
    raise ValueError("boom")


def _half_open_breaker() -> CircuitBreaker:
    return CircuitBreaker(
        "t",
        CircuitBreakerConfig(failure_threshold=1, success_threshold=2, timeout=0.0),
    )


async def _open(cb: CircuitBreaker) -> None:
    with pytest.raises(ValueError):
        await cb.call(_boom)
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_half_open_closes_after_success_threshold_probes() -> None:
    cb = _half_open_breaker()
    await _open(cb)

    assert await cb.call(_ok) == 1  # first probe: HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN
    assert await cb.call(_ok) == 1  # second probe must be admitted
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_failed_probe_releases_slot_and_reopens() -> None:
    cb = _half_open_breaker()
    await _open(cb)

    with pytest.raises(ValueError):
        await cb.call(_boom)
    assert cb.state == CircuitState.OPEN
    # timeout=0: next call goes HALF_OPEN again and is admitted, not rejected.
    assert await cb.call(_ok) == 1


@pytest.mark.asyncio
async def test_cancelled_probe_releases_slot() -> None:
    cb = _half_open_breaker()
    await _open(cb)

    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(cb.call(hang))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cb.state == CircuitState.HALF_OPEN
    assert await cb.call(_ok) == 1  # slot was released, not leaked


@pytest.mark.asyncio
async def test_half_open_still_limits_concurrent_probes() -> None:
    cb = _half_open_breaker()
    await _open(cb)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> int:
        started.set()
        await release.wait()
        return 1

    first = asyncio.create_task(cb.call(slow))
    await started.wait()
    with pytest.raises(Exception, match="HALF_OPEN"):
        await cb.call(_ok)
    release.set()
    assert await first == 1


@pytest.mark.asyncio
async def test_masr_routing_breaker_opens_after_consecutive_failures() -> None:
    router = MASRouter(config={"circuit_failure_threshold": 3})
    calls = 0

    async def fail(*_a, **_k):
        nonlocal calls
        calls += 1
        raise RuntimeError("analyzer down")

    router.complexity_analyzer.analyze = fail  # type: ignore[method-assign]

    for _ in range(3):
        await router.route("q")  # failures return a fallback decision
    assert router.routing_circuit_breaker.state == CircuitState.OPEN
    assert calls == 3

    await router.route("q")  # rejected by the open breaker
    assert calls == 3
