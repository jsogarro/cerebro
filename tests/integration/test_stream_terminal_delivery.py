"""Regression coverage for a terminal-event delivery race in ``stream()``.

``RunLifecycleRepository`` / ``DirectExecutionService._persist_transition``
commit a run's terminal event and its terminal status together, in one
transaction. ``RunEventStreamReader.stream()`` used to read a batch and only
*afterwards* ask whether the run is terminal — so a commit landing in that
exact window (batch read finds nothing yet, terminal commit lands, terminal
check then sees it) made the generator return cleanly without ever yielding
the event that made the run terminal. No error, no signal: a WebSocket
client (or anything else treating a clean close as "stream complete") would
silently lose the run's outcome. SSE clients happened to recover only
because ``EventSource`` auto-reconnects with ``Last-Event-ID``.

This reproduces the exact interleaving directly against the reader's own
methods (real Postgres, via ``test_engine``), rather than relying on timing.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.websocket.run_stream import RunEventStreamReader, RunStreamEntitlement
from src.core.contracts import Run, RunEventCursor, RunStatus
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 4, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000f4")


def _make_run(run_id: str) -> Run:
    return Run(
        run_id=run_id,
        tenant_id=str(ORG_ID),
        workflow_definition_id="research",
        workflow_definition_version="1",
        routing_policy_id="default",
        routing_policy_version="1",
        idempotency_key=f"submit-{run_id}",
        requested_by="user-1",
        status=RunStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession], run_id: str
) -> None:
    async with session_factory() as session:
        await RunLifecycleRepository(session).create_run(
            _make_run(run_id), organization_id=ORG_ID
        )
        await session.commit()


async def _commit_terminal_event_and_status(
    session_factory: async_sessionmaker[AsyncSession], run_id: str
) -> None:
    """Commit one event and the terminal status transition together.

    Mirrors the real production shape this race depends on: the event and
    the status flip land in the *same* transaction, so a reader can only ever
    observe them atomically, never the status without the event.
    """
    run = _make_run(run_id)
    async with session_factory() as session:
        event_repo = RunEventRepository(session)
        lifecycle_repo = RunLifecycleRepository(session)
        await event_repo.append_event(
            run_id=run_id,
            organization_id=ORG_ID,
            event_id=f"{run_id}-succeeded",
            aggregate_type="run",
            aggregate_id=run_id,
            event_type="run.succeeded",
            event_type_version="1",
            occurred_at=NOW,
            producer="test",
            deduplication_key=f"{run_id}-succeeded",
            payload={"label": "done"},
            destinations=(),
        )
        for target in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUCCEEDED):
            run = run.transition_to(target, at=NOW)
            await lifecycle_repo.record_run_transition(run, organization_id=ORG_ID)
        await session.commit()


@pytest.mark.asyncio
async def test_stream_terminal_delivery_survives_the_batch_then_terminal_race(
    test_engine: AsyncEngine,
) -> None:
    """A commit landing between an empty batch read and the terminal check
    must not be lost: the reader must yield the terminal event before it
    stops, not return silently.
    """
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "run-terminal-race"
    await _seed_run(session_factory, run_id)

    reader = RunEventStreamReader(
        session_factory=session_factory,
        entitlement=RunStreamEntitlement(organization_id=ORG_ID),
        destination="websocket",
        poll_interval_seconds=0.05,
    )

    # Reproduce the exact interleaving: the batch read (called first, inside
    # stream()) has already returned []. Only *after* that do we commit the
    # terminal event + status — landing precisely in the window between the
    # batch read and the terminal check that follows it.
    original_is_run_terminal = reader.is_run_terminal

    async def _terminal_check_that_races_a_commit(run_id_arg: str) -> bool:
        await _commit_terminal_event_and_status(session_factory, run_id)
        return await original_is_run_terminal(run_id_arg)

    reader.is_run_terminal = _terminal_check_that_races_a_commit  # type: ignore[method-assign]

    frames = [
        frame
        async for frame in reader.stream(
            run_id, cursor=RunEventCursor(run_id=run_id), max_idle_seconds=5.0
        )
    ]

    # Before the fix: frames == [] — the terminal event committed in the
    # race window was never read, and the generator returned silently.
    assert [frame.payload["label"] for frame in frames] == ["done"]
    assert [frame.event_type for frame in frames] == ["run.succeeded"]
