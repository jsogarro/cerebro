import asyncio
from collections.abc import Coroutine
from typing import Any


class BackgroundTaskTracker:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def create_task(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def cancel_all(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
