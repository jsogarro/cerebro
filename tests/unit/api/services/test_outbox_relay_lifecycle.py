"""Unit coverage for ``OutboxRelay``'s background task lifecycle.

The claim/deliver/finalize SQL behavior needs a real database and is covered
in ``tests/integration/test_outbox_relay.py``; this only exercises
``start``/``stop`` idempotency and that the polling loop actually calls
``run_once`` without needing any real persistence underneath it.
"""

import asyncio

import pytest

from src.api.services.outbox_relay import OutboxRelay


class _CountingRelay(OutboxRelay):
    """Overrides ``run_once`` so the loop never touches a real session."""

    def __init__(self) -> None:
        super().__init__(
            session_factory=object(),  # never called
            event_publisher=object(),  # never called
            poll_interval_seconds=0.01,
        )
        self.calls = 0
        self.release = asyncio.Event()

    async def run_once(self) -> int:
        self.calls += 1
        if self.calls >= 3:
            self.release.set()
        return 0


@pytest.mark.asyncio
async def test_start_polls_run_once_repeatedly_until_stopped() -> None:
    relay = _CountingRelay()

    relay.start()
    await asyncio.wait_for(relay.release.wait(), timeout=2)
    await relay.stop()

    assert relay.calls >= 3


@pytest.mark.asyncio
async def test_start_is_idempotent_while_already_running() -> None:
    relay = _CountingRelay()

    relay.start()
    first_task = relay._task
    relay.start()  # must not spawn a second loop

    assert relay._task is first_task

    await relay.stop()


@pytest.mark.asyncio
async def test_stop_before_start_does_not_raise() -> None:
    relay = _CountingRelay()

    await relay.stop()

    assert relay._task is None
