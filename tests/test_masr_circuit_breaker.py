"""MASR's routing breaker must open, reject while open, and never wedge half-open.

MASR and ``GeminiService`` share one breaker implementation, the state-visible
``src.core.tools.circuit.CircuitBreaker``: it admits exactly one trial call after
the cooldown, closes on that trial's success and reopens on its failure. The
tests drive the breaker through ``MASRouter.route``, which is how it is used.
"""

from __future__ import annotations

import asyncio

import pytest

from src.ai_brain.router.masr import MASRouter
from src.core.tools.circuit import BreakerState, CircuitBreaker


def _router(threshold: int = 1, cooldown: float = 0.05) -> MASRouter:
    router = MASRouter(config={"enable_learning": False})
    router._routing_circuit_breaker = CircuitBreaker(
        failure_threshold=threshold, cooldown_seconds=cooldown
    )
    return router


class _Analyzer:
    """Stands in for the complexity analyzer so routing can be failed on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = True
        self.hang: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def analyze(self, *_a: object, **_k: object) -> object:
        self.calls += 1
        self.started.set()
        if self.hang is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("analyzer down")
        raise RuntimeError("recovered but not under test")


async def _cooldown() -> None:
    await asyncio.sleep(0.08)


@pytest.mark.asyncio
async def test_masr_routing_breaker_opens_after_consecutive_failures() -> None:
    router = MASRouter(config={"circuit_failure_threshold": 3})
    analyzer = _Analyzer()
    router.complexity_analyzer.analyze = analyzer.analyze  # type: ignore[method-assign]

    for _ in range(3):
        await router.route("q")  # failures return a fallback decision
    assert router.routing_circuit_breaker.state is BreakerState.OPEN
    assert analyzer.calls == 3

    await router.route("q")  # rejected by the open breaker
    assert analyzer.calls == 3


@pytest.mark.asyncio
async def test_an_open_breaker_rejects_without_routing() -> None:
    router = _router(cooldown=60)
    analyzer = _Analyzer()
    router.complexity_analyzer.analyze = analyzer.analyze  # type: ignore[method-assign]
    await router.route("q")
    assert router.routing_circuit_breaker.state is BreakerState.OPEN

    decision = await router.route("q")

    assert analyzer.calls == 1
    assert decision is not None  # the caller still gets its fallback decision


@pytest.mark.asyncio
async def test_failed_trial_reopens_and_a_later_trial_is_admitted() -> None:
    router = _router()
    analyzer = _Analyzer()
    router.complexity_analyzer.analyze = analyzer.analyze  # type: ignore[method-assign]
    await router.route("q")
    await _cooldown()

    await router.route("q")  # the one trial fails
    assert router.routing_circuit_breaker.state is BreakerState.OPEN
    assert analyzer.calls == 2
    await _cooldown()

    await router.route("q")  # the slot was released, so a new trial is admitted
    assert analyzer.calls == 3


@pytest.mark.asyncio
async def test_cancelled_trial_releases_the_slot() -> None:
    router = _router()
    analyzer = _Analyzer()
    router.complexity_analyzer.analyze = analyzer.analyze  # type: ignore[method-assign]
    await router.route("q")
    await _cooldown()

    analyzer.hang = asyncio.Event()
    analyzer.started.clear()
    task = asyncio.create_task(router.route("q"))
    await asyncio.wait_for(analyzer.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    analyzer.hang = None
    before = analyzer.calls
    await router.route("q")  # not rejected: cancellation gave the slot back
    assert analyzer.calls == before + 1


@pytest.mark.asyncio
async def test_half_open_admits_only_one_concurrent_trial() -> None:
    router = _router()
    analyzer = _Analyzer()
    router.complexity_analyzer.analyze = analyzer.analyze  # type: ignore[method-assign]
    await router.route("q")
    await _cooldown()

    analyzer.hang = asyncio.Event()
    analyzer.started.clear()
    first = asyncio.create_task(router.route("q"))
    await asyncio.wait_for(analyzer.started.wait(), 2)
    before = analyzer.calls
    await router.route("q")  # rejected: the trial is in flight
    assert analyzer.calls == before

    analyzer.release.set()
    await first
