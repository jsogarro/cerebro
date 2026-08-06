"""Integration tests for ``RunEventRepository`` against real Postgres.

Covers the properties that only a real database can prove: gap-free,
monotonic sequence allocation under genuine concurrent connections, and that
an event row and its outbox deliveries are written atomically — an outbox row
never exists for an event that didn't get committed.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.core.contracts import Run, RunStatus
from src.models.db.run_event import AgentRunEvent, AgentRunEventOutbox
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 4, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def _make_run(**overrides: object) -> Run:
    values: dict[str, object] = {
        "run_id": "run-1",
        "tenant_id": str(ORG_ID),
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
        "idempotency_key": "submit-1",
        "requested_by": "user-1",
        "status": RunStatus.CREATED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Run(**values)


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> RunEventRepository:
    return RunEventRepository(db_session)


@pytest_asyncio.fixture(name="seeded_run")
async def seeded_run_fixture(db_session: AsyncSession) -> None:
    await RunLifecycleRepository(db_session).create_run(
        _make_run(), organization_id=ORG_ID
    )
    await db_session.flush()


def _event_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "run_id": "run-1",
        "organization_id": ORG_ID,
        "event_id": "event-1",
        "aggregate_type": "run",
        "aggregate_id": "run-1",
        "event_type": "run.created",
        "event_type_version": "1",
        "occurred_at": NOW,
        "producer": "test-producer",
        "deduplication_key": "run-1:1:run.created",
        "payload": {"status": "created"},
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_append_event_allocates_sequence_starting_at_one(
    repo: RunEventRepository, seeded_run: None
) -> None:
    row = await repo.append_event(**_event_kwargs())

    assert row.sequence == 1
    assert row.event_id == "event-1"


@pytest.mark.asyncio
async def test_append_event_allocates_increasing_sequences(
    repo: RunEventRepository, seeded_run: None
) -> None:
    first = await repo.append_event(**_event_kwargs())
    second = await repo.append_event(
        **_event_kwargs(event_id="event-2", deduplication_key="run-1:2:run.updated")
    )

    assert first.sequence == 1
    assert second.sequence == 2


@pytest.mark.asyncio
async def test_append_event_fails_closed_without_an_org_context(
    repo: RunEventRepository, seeded_run: None
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.append_event(**_event_kwargs(organization_id=None))


@pytest.mark.asyncio
async def test_append_event_rejects_a_mismatched_org_context(
    repo: RunEventRepository, seeded_run: None
) -> None:
    with pytest.raises(TenantMismatchError):
        await repo.append_event(**_event_kwargs(organization_id=OTHER_ORG_ID))


@pytest.mark.asyncio
async def test_append_event_requires_the_run_to_exist(repo: RunEventRepository) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        await repo.append_event(**_event_kwargs())


@pytest.mark.asyncio
async def test_append_event_inserts_one_outbox_row_per_destination(
    repo: RunEventRepository, seeded_run: None, db_session: AsyncSession
) -> None:
    await repo.append_event(**_event_kwargs(destinations=("websocket", "sse")))

    result = await db_session.execute(
        select(AgentRunEventOutbox).where(AgentRunEventOutbox.event_id == "event-1")
    )
    outbox_rows = list(result.scalars().all())

    assert {row.destination for row in outbox_rows} == {"websocket", "sse"}
    for row in outbox_rows:
        assert row.idempotency_key == f"{row.destination}:run-1:1:run.created"
        assert row.payload["event_id"] == "event-1"


@pytest.mark.asyncio
async def test_read_events_after_replays_in_sequence_order(
    repo: RunEventRepository, seeded_run: None
) -> None:
    await repo.append_event(**_event_kwargs())
    await repo.append_event(
        **_event_kwargs(event_id="event-2", deduplication_key="run-1:2:run.updated")
    )
    await repo.append_event(
        **_event_kwargs(event_id="event-3", deduplication_key="run-1:3:run.updated")
    )

    replayed = await repo.read_events_after("run-1", after_sequence=1)

    assert [event.sequence for event in replayed] == [2, 3]
    assert [event.event_id for event in replayed] == ["event-2", "event-3"]


@pytest.mark.asyncio
async def test_read_events_after_from_zero_returns_everything(
    repo: RunEventRepository, seeded_run: None
) -> None:
    await repo.append_event(**_event_kwargs())

    replayed = await repo.read_events_after("run-1", after_sequence=0)

    assert [event.sequence for event in replayed] == [1]


# --- atomicity: outbox row exists iff the event row does -------------------


@pytest.mark.asyncio
async def test_outbox_row_does_not_survive_a_rolled_back_event_insert(
    repo: RunEventRepository, seeded_run: None, db_session: AsyncSession
) -> None:
    await repo.append_event(**_event_kwargs(destinations=("websocket",)))
    await db_session.commit()

    # A second append with the same deduplication_key violates the per-run
    # unique constraint on the event row; the outbox insert for the *same*
    # flush must never survive that failure.
    with pytest.raises(IntegrityError):
        await repo.append_event(
            **_event_kwargs(event_id="event-2", destinations=("websocket",))
        )
    await db_session.rollback()

    event_result = await db_session.execute(select(AgentRunEvent))
    outbox_result = await db_session.execute(select(AgentRunEventOutbox))

    assert len(list(event_result.scalars().all())) == 1
    assert len(list(outbox_result.scalars().all())) == 1


# --- concurrency: gap-free, monotonic sequence allocation -------------------


@pytest.mark.asyncio
async def test_concurrent_appends_allocate_gap_free_monotonic_sequences(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as setup_session:
        await RunLifecycleRepository(setup_session).create_run(
            _make_run(), organization_id=ORG_ID
        )
        await setup_session.commit()

    concurrency = 10

    async def _append(index: int) -> int:
        # A bare `async with session_factory()` still relies on this
        # coroutine running to completion to release its connection. Under
        # `asyncio.gather` without `return_exceptions=True`, one task raising
        # cancels every sibling still mid-await — including mid-transaction —
        # which can leave a connection checked out with an open row lock on
        # `agent_runs` that then hangs this same test's `test_engine`
        # teardown (`DROP TABLE agent_runs` blocks on it forever). The
        # explicit rollback-on-error guarantees the lock is released even if
        # a sibling task's failure triggers cancellation here.
        async with session_factory() as session:
            try:
                repo = RunEventRepository(session)
                row = await repo.append_event(
                    **_event_kwargs(
                        event_id=f"event-{index}",
                        deduplication_key=f"run-1:{index}:concurrent",
                    )
                )
                await session.commit()
                return row.sequence
            except BaseException:
                await session.rollback()
                raise

    results = await asyncio.gather(
        *[_append(index) for index in range(concurrency)], return_exceptions=True
    )

    errors = [result for result in results if isinstance(result, BaseException)]
    assert errors == []
    sequences = [result for result in results if isinstance(result, int)]
    assert sorted(sequences) == list(range(1, concurrency + 1))
