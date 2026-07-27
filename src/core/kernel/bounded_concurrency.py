"""Inspectable, admission-bounded async task execution for the kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Generic, TypeVar, cast

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


class BoundedTaskRunner(Generic[ItemT, ResultT]):
    """Run work concurrently without allocating tasks for queued items.

    The runner retains input-order results.  It intentionally creates only up
    to ``max_concurrency`` tasks at a time; remaining input values are queued
    as values rather than pre-created coroutines or tasks.
    """

    def __init__(self, max_concurrency: int) -> None:
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int):
            raise TypeError(
                "max_concurrency must be an integer; "
                f"got {type(max_concurrency).__name__}"
            )
        if max_concurrency <= 0:
            raise ValueError(f"max_concurrency must be positive; got {max_concurrency}")

        self.max_concurrency = max_concurrency
        self._active_tasks: set[asyncio.Task[ResultT]] = set()
        self._queued_count = 0
        self._created_count = 0
        self._peak_active_count = 0
        self._running = False

    @property
    def active_count(self) -> int:
        """Return the number of admitted tasks that have not completed."""

        return len(self._active_tasks)

    @property
    def queued_count(self) -> int:
        """Return the number of input values awaiting task admission."""

        return self._queued_count

    @property
    def created_count(self) -> int:
        """Return tasks created during the current or most recent run."""

        return self._created_count

    @property
    def peak_active_count(self) -> int:
        """Return the high-water mark of admitted work for the current run."""

        return self._peak_active_count

    async def run(
        self,
        items: Iterable[ItemT],
        operation: Callable[[ItemT], Awaitable[ResultT]],
        *,
        return_exceptions: bool = False,
    ) -> list[ResultT | BaseException]:
        """Run ``operation`` for each item with bounded task admission.

        ``return_exceptions`` mirrors :func:`asyncio.gather` for ordinary
        worker failures while preserving cancellation: cancelling the caller
        always cancels admitted work, clears queued work, and propagates the
        cancellation instead of turning it into a result value.
        """

        if self._running:
            raise RuntimeError("BoundedTaskRunner cannot run more than once at a time")

        queued_items = tuple(items)
        results: list[ResultT | BaseException | None] = [None] * len(queued_items)
        task_indices: dict[asyncio.Task[ResultT], int] = {}
        next_index = 0
        self._running = True
        self._queued_count = len(queued_items)
        self._created_count = 0
        self._peak_active_count = 0

        async def invoke(item: ItemT) -> ResultT:
            return await operation(item)

        def admit_next() -> bool:
            nonlocal next_index
            if next_index >= len(queued_items):
                return False

            task: asyncio.Task[ResultT] = asyncio.create_task(
                invoke(queued_items[next_index])
            )
            task_indices[task] = next_index
            self._active_tasks.add(task)
            next_index += 1
            self._queued_count -= 1
            self._created_count += 1
            self._peak_active_count = max(
                self._peak_active_count,
                self.active_count,
            )
            return True

        async def cancel_admitted_work() -> None:
            tasks = tuple(self._active_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            while self.active_count < self.max_concurrency and admit_next():
                pass

            while self._active_tasks:
                completed, _ = await asyncio.wait(
                    self._active_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in sorted(completed, key=task_indices.__getitem__):
                    index = task_indices.pop(task)
                    self._active_tasks.remove(task)
                    try:
                        results[index] = task.result()
                    except BaseException as exc:
                        if not return_exceptions:
                            raise
                        results[index] = exc

                # A wait result can contain a batch of simultaneously completed
                # tasks.  Process all of it before releasing any capacity so a
                # fail-fast error cannot admit queued work after a sibling
                # succeeds in that same batch.
                while self.active_count < self.max_concurrency and admit_next():
                    pass

            return cast(list[ResultT | BaseException], results)
        except BaseException:
            self._queued_count = 0
            await cancel_admitted_work()
            raise
        finally:
            self._active_tasks.clear()
            self._running = False
