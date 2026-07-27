"""Deterministic contracts for kernel-owned bounded task admission."""

import asyncio
from collections.abc import Callable

import pytest

from src.core.kernel import BoundedTaskRunner


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Yield until a deterministic test predicate becomes true."""

    while not predicate():
        await asyncio.sleep(0)


def test_runner_rejects_non_positive_limits_synchronously() -> None:
    with pytest.raises(ValueError, match=r"^max_concurrency must be positive; got 0$"):
        BoundedTaskRunner(0)

    with pytest.raises(ValueError, match=r"^max_concurrency must be positive; got -1$"):
        BoundedTaskRunner(-1)


@pytest.mark.asyncio
async def test_runner_admits_only_the_limit_before_creating_queued_tasks() -> None:
    runner = BoundedTaskRunner(2)
    started: list[int] = []
    release = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_task_factory = loop.get_task_factory()
    created_tasks = 0

    def count_created_tasks(
        loop: asyncio.AbstractEventLoop,
        coroutine: object,
        context: object = None,
    ) -> asyncio.Task[object]:
        nonlocal created_tasks
        created_tasks += 1
        return asyncio.Task(coroutine, loop=loop, context=context)  # type: ignore[arg-type]

    async def work(value: int) -> int:
        started.append(value)
        await release.wait()
        return value

    loop.set_task_factory(count_created_tasks)
    try:
        execution = asyncio.create_task(runner.run(range(5), work))
        await _wait_until(lambda: len(started) == 2)

        assert started == [0, 1]
        assert created_tasks == 3  # The caller plus exactly two admitted workers.
        assert runner.created_count == 2
        assert runner.active_count == 2
        assert runner.queued_count == 3
        assert runner.peak_active_count == 2

        release.set()

        assert await execution == [0, 1, 2, 3, 4]
        assert runner.created_count == 5
        assert runner.active_count == 0
        assert runner.queued_count == 0
    finally:
        loop.set_task_factory(previous_task_factory)


@pytest.mark.asyncio
async def test_runner_preserves_input_order_when_work_completes_out_of_order() -> None:
    runner = BoundedTaskRunner(2)
    release_first = asyncio.Event()

    async def work(value: int) -> int:
        if value == 0:
            await release_first.wait()
        return value

    execution = asyncio.create_task(runner.run(range(3), work))
    await _wait_until(lambda: runner.created_count == 2)
    await _wait_until(lambda: runner.active_count == 1)

    assert runner.created_count == 3

    release_first.set()

    assert await execution == [0, 1, 2]


@pytest.mark.asyncio
async def test_runner_records_exceptions_and_releases_slots_when_requested() -> None:
    runner = BoundedTaskRunner(2)
    started: list[int] = []

    async def work(value: int) -> int:
        started.append(value)
        if value == 1:
            raise RuntimeError("expected failure")
        return value

    results = await runner.run(range(4), work, return_exceptions=True)

    assert results[0] == 0
    assert isinstance(results[1], RuntimeError)
    assert results[2:] == [2, 3]
    assert started == [0, 1, 2, 3]
    assert runner.created_count == 4
    assert runner.active_count == 0
    assert runner.queued_count == 0


@pytest.mark.asyncio
async def test_runner_records_child_cancellation_and_continues_when_requested() -> None:
    """A cancelled worker is a result value, unlike caller cancellation."""
    runner = BoundedTaskRunner(2)
    started: list[int] = []
    release = asyncio.Event()

    async def work(value: int) -> int:
        started.append(value)
        if value == 0:
            raise asyncio.CancelledError("worker cancelled")
        await release.wait()
        return value

    execution = asyncio.create_task(runner.run(range(3), work, return_exceptions=True))
    await _wait_until(lambda: execution.done() or started == [0, 1, 2])

    assert not execution.done()

    release.set()

    results = await execution

    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1:] == [1, 2]
    assert runner.created_count == 3
    assert runner.active_count == 0
    assert runner.queued_count == 0


@pytest.mark.asyncio
async def test_runner_fail_fast_checks_simultaneous_batch_before_admitting_queue() -> (
    None
):
    runner = BoundedTaskRunner(2)
    started: list[int] = []
    release = asyncio.Event()

    async def work(value: int) -> int:
        started.append(value)
        await release.wait()
        if value == 1:
            raise RuntimeError("simultaneous failure")
        return value

    execution = asyncio.create_task(runner.run(range(3), work))
    await _wait_until(lambda: started == [0, 1])
    release.set()

    with pytest.raises(RuntimeError, match="simultaneous failure"):
        await execution

    assert started == [0, 1]
    assert runner.created_count == 2
    assert runner.active_count == 0
    assert runner.queued_count == 0


@pytest.mark.asyncio
async def test_runner_propagates_caller_cancellation_without_admitting_queue() -> None:
    runner = BoundedTaskRunner(2)
    started: list[int] = []
    cancelled: list[int] = []
    release = asyncio.Event()

    async def work(value: int) -> int:
        started.append(value)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(value)
            raise
        return value

    execution = asyncio.create_task(runner.run(range(5), work, return_exceptions=True))
    await _wait_until(lambda: len(started) == 2)
    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert started == [0, 1]
    assert sorted(cancelled) == [0, 1]
    assert runner.created_count == 2
    assert runner.active_count == 0
    assert runner.queued_count == 0
