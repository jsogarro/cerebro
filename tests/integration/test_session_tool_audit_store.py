"""Integration coverage for the session-backed tool audit store.

These tests deliberately exercise a real ``ToolBoundary`` and the real
PostgreSQL repositories.  The boundary writes an allowed invocation twice
(``REQUESTED`` and terminal), while a refusal writes it once; the adapter has
to map both cases onto one invocation row without weakening the transaction or
tenant boundaries.

Tool audit events use ``EVENT_TYPE_VERSION`` (currently ``"1.0"``) as their
canonical event-type version.  The direct execution service's existing
``"1"`` values describe its legacy run-lifecycle events, not this tool-event
family, and remain outside this packet.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.agents.tools.durable_audit import SessionToolAuditStore
from src.agents.tools.mediation import (
    InMemoryToolAuditStore,
    ToolCallIdentity,
    build_tool_boundary,
)
from src.core.contracts import (
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityGrant,
    SensitivityClass,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.core.tools import (
    EVENT_COMPLETED,
    EVENT_REQUESTED,
    EVENT_TYPE_VERSION,
    MappingSecretProvider,
    NullEventPublisher,
    ToolAuditEvent,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)
from src.models.db.run_event import AgentRunEvent
from src.models.db.tool_invocation import AgentToolInvocation
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.tenant_scope import MissingOrganizationContextError
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
RUN_ID = "run-1"
TASK_ID = "task-1"
ATTEMPT_ID = "attempt-1"
TOOL = "academic_search"
VERSION = "1.0"
SCOPE = "search"
GRANT_ID = "grant-1"


class SearchInput(BaseModel):
    query: str


class SearchOutput(BaseModel):
    hits: int


async def search(args: SearchInput, context: ToolCallContext) -> dict[str, Any]:
    return {"hits": 1}


@dataclass(slots=True)
class DatabaseObservingPublisher:
    """A publisher that can only observe rows visible after a commit."""

    session_factory: async_sessionmaker[AsyncSession]
    observed: list[tuple[str, str | None]] = field(default_factory=list)

    async def publish(self, events: Sequence[ToolAuditEvent]) -> None:
        for event in events:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(AgentToolInvocation.status).where(
                        AgentToolInvocation.tool_invocation_id == event.aggregate_id
                    )
                )
                self.observed.append((event.event_type, result.scalar_one_or_none()))


@dataclass(slots=True)
class CrashBeforeTerminalPersistStore:
    """Commit REQUESTED, then simulate a process crash before terminal write."""

    inner: SessionToolAuditStore

    async def find_invocation(self, **kwargs: Any) -> ToolInvocation | None:
        return await self.inner.find_invocation(**kwargs)

    async def find_pending_invocation(self, **kwargs: Any) -> ToolInvocation | None:
        return await self.inner.find_pending_invocation(**kwargs)

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        if invocation.status is ToolInvocationStatus.SUCCEEDED:
            raise RuntimeError("simulated crash before terminal persist")
        await self.inner.persist(
            invocation=invocation,
            events=events,
            organization_id=organization_id,
            capability_decision=capability_decision,
        )


@pytest_asyncio.fixture(name="session_factory")
async def session_factory_fixture(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _grant() -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=GRANT_ID,
        run_id=RUN_ID,
        task_id=TASK_ID,
        capability_scope=SCOPE,
        tool_name=TOOL,
        tool_versions=(VERSION,),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requires_approval=False,
        issued_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=365),
    )


@pytest_asyncio.fixture(name="seeded", autouse=True)
async def seeded_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_run_task_attempt(session, organization_id=ORG_ID)
        await CapabilityRepository(session).create_grant(
            _grant(), organization_id=ORG_ID
        )
        await session.commit()


@pytest_asyncio.fixture(name="store")
async def store_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> SessionToolAuditStore:
    return SessionToolAuditStore(session_factory)


def _boundary(
    store: Any,
    publisher: Any | None = None,
    handler: Any = search,
) -> ToolBoundary:
    boundary = ToolBoundary(
        secret_provider=MappingSecretProvider({}),
        audit_store=store,
        event_publisher=publisher or NullEventPublisher(),
        clock=lambda: NOW,
    )
    boundary.register(
        ToolSpec(
            name=TOOL,
            version=VERSION,
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=SearchInput,
            output_model=SearchOutput,
            timeout_seconds=5.0,
            handler=handler,  # type: ignore[arg-type]
        )
    )
    return boundary


@pytest.fixture(name="boundary")
def boundary_fixture(store: SessionToolAuditStore) -> ToolBoundary:
    return _boundary(store)


def _call_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tool_name": TOOL,
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "attempt_id": ATTEMPT_ID,
        "organization_id": str(ORG_ID),
        "capability_scope": SCOPE,
        "arguments": {"query": "graph neural networks"},
        "input_trust": TrustClassification.USER_SUPPLIED,
        "grants": [],
    }
    values.update(overrides)
    return values


def _grants() -> list[CapabilityGrant]:
    return [_grant()]


def _allow_decision() -> CapabilityDecision:
    return CapabilityDecision(
        effect=CapabilityDecisionEffect.ALLOW,
        request_fingerprint="a" * 64,
        grant_id=GRANT_ID,
        decided_at=NOW,
    )


def _requested_invocation(
    *,
    invocation_id: str = "manual-invocation-1",
    idempotency_key: str = "manual-key-1",
    status: ToolInvocationStatus = ToolInvocationStatus.REQUESTED,
) -> ToolInvocation:
    return ToolInvocation(
        tool_invocation_id=invocation_id,
        run_id=RUN_ID,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        tool_name=TOOL,
        tool_version=VERSION,
        status=status,
        capability_scope=SCOPE,
        idempotency_key=idempotency_key,
        input={"query": "atomic"},
        input_trust=TrustClassification.USER_SUPPLIED,
        output=({"hits": 1} if status is ToolInvocationStatus.SUCCEEDED else None),
        output_trust=(
            TrustClassification.EXTERNAL_UNTRUSTED
            if status is ToolInvocationStatus.SUCCEEDED
            else None
        ),
        requested_at=NOW,
        completed_at=NOW if status is ToolInvocationStatus.SUCCEEDED else None,
    )


def _event(
    *,
    event_id: str,
    invocation: ToolInvocation,
    event_type: str,
    payload: dict[str, Any],
) -> ToolAuditEvent:
    return ToolAuditEvent(
        event_id=event_id,
        run_id=invocation.run_id,
        task_id=invocation.task_id,
        attempt_id=invocation.attempt_id,
        aggregate_id=invocation.tool_invocation_id,
        event_type=event_type,
        occurred_at=invocation.completed_at or invocation.requested_at,
        producer="integration-test",
        deduplication_key=f"{event_id}:dedup",
        payload=payload,
    )


@pytest.mark.parametrize(
    ("field_name", "wrong_value"),
    [
        ("run_id", "other-run"),
        ("task_id", "other-task"),
        ("attempt_id", "other-attempt"),
        ("aggregate_id", "other-invocation"),
    ],
)
@pytest.mark.asyncio
async def test_tool_event_association_must_match_invocation(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
    field_name: str,
    wrong_value: str,
) -> None:
    invocation = _requested_invocation(
        invocation_id=f"association-{field_name}",
        idempotency_key=f"association-key-{field_name}",
    )
    event = replace(
        _event(
            event_id=f"association-event-{field_name}",
            invocation=invocation,
            event_type=EVENT_REQUESTED,
            payload={"phase": "requested"},
        ),
        **{field_name: wrong_value},
    )

    with pytest.raises(
        ValueError,
        match=f"event {field_name} does not match invocation",
    ):
        await store.persist(
            invocation=invocation,
            events=(event,),
            organization_id=ORG_ID,
            capability_decision=_allow_decision(),
        )

    async with session_factory() as session:
        assert list((await session.scalars(select(AgentToolInvocation))).all()) == []
        assert list((await session.scalars(select(AgentRunEvent))).all()) == []


@pytest.mark.asyncio
async def test_an_allowed_call_writes_one_row_and_two_events(
    boundary: ToolBoundary,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    outcome = await boundary.invoke(**_call_kwargs(grants=_grants()))

    assert outcome.succeeded
    async with session_factory() as session:
        invocations = list(
            (
                await session.scalars(
                    select(AgentToolInvocation).where(
                        AgentToolInvocation.run_id == RUN_ID
                    )
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.aggregate_id
                        == outcome.invocation.tool_invocation_id
                    )
                    .order_by(AgentRunEvent.sequence)
                )
            ).all()
        )

    assert len(invocations) == 1
    assert invocations[0].status == ToolInvocationStatus.SUCCEEDED.value
    assert [event.event_type for event in events] == [
        EVENT_REQUESTED,
        EVENT_COMPLETED,
    ]
    assert all(event.event_type_version == EVENT_TYPE_VERSION for event in events)


@pytest.mark.asyncio
async def test_a_refused_call_writes_one_row_and_one_event(
    boundary: ToolBoundary,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    outcome = await boundary.invoke(**_call_kwargs())

    assert outcome.status.value == ToolInvocationStatus.DENIED.value
    async with session_factory() as session:
        invocations = list((await session.scalars(select(AgentToolInvocation))).all())
        events = list((await session.scalars(select(AgentRunEvent))).all())

    assert len(invocations) == 1
    assert invocations[0].status == ToolInvocationStatus.DENIED.value
    assert [event.event_type for event in events] == [EVENT_COMPLETED]


@pytest.mark.asyncio
async def test_the_invocation_and_its_events_land_or_neither_does(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = _requested_invocation()
    events = (
        _event(
            event_id="atomic-event-1",
            invocation=invocation,
            event_type=EVENT_REQUESTED,
            payload={"phase": "requested"},
        ),
        _event(
            event_id="atomic-event-2",
            invocation=invocation,
            event_type=EVENT_COMPLETED,
            payload={"phase": "completed"},
        ),
    )
    original_append_event = RunEventRepository.append_event
    append_count = 0

    async def append_then_fail(repository: RunEventRepository, **kwargs: Any) -> Any:
        nonlocal append_count
        append_count += 1
        row = await original_append_event(repository, **kwargs)
        if append_count == 2:
            raise RuntimeError("event append failed after flush")
        return row

    monkeypatch.setattr(RunEventRepository, "append_event", append_then_fail)

    with pytest.raises(RuntimeError, match="event append failed"):
        await store.persist(
            invocation=invocation,
            events=events,
            organization_id=str(ORG_ID),
            capability_decision=_allow_decision(),
        )

    async with session_factory() as session:
        invocations = list((await session.scalars(select(AgentToolInvocation))).all())
        stored_events = list((await session.scalars(select(AgentRunEvent))).all())

    assert invocations == []
    assert stored_events == []


@pytest.mark.asyncio
async def test_nothing_is_published_when_the_write_fails(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = DatabaseObservingPublisher(session_factory)
    boundary = _boundary(store, publisher)
    original_append_event = RunEventRepository.append_event

    async def fail_append(repository: RunEventRepository, **kwargs: Any) -> Any:
        await original_append_event(repository, **kwargs)
        raise RuntimeError("event append failed")

    monkeypatch.setattr(RunEventRepository, "append_event", fail_append)

    with pytest.raises(RuntimeError, match="event append failed"):
        await boundary.invoke(**_call_kwargs(grants=_grants()))

    assert publisher.observed == []


@pytest.mark.asyncio
async def test_a_tool_event_is_replayable_from_the_run_event_stream(
    boundary: ToolBoundary,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    outcome = await boundary.invoke(**_call_kwargs(grants=_grants()))

    async with session_factory() as session:
        events = list(
            (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(
                        AgentRunEvent.aggregate_id
                        == outcome.invocation.tool_invocation_id
                    )
                    .order_by(AgentRunEvent.sequence)
                )
            ).all()
        )

    assert len(events) == 2
    assert all(event.payload["task_id"] == TASK_ID for event in events)
    assert all(event.payload["attempt_id"] == ATTEMPT_ID for event in events)
    assert events[0].payload["tool_name"] == TOOL
    assert events[1].payload["outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_a_terminal_invocation_is_replayable_and_tenant_scoped(
    store: SessionToolAuditStore,
) -> None:
    requested = _requested_invocation()
    await store.persist(
        invocation=requested,
        events=(
            _event(
                event_id="replay-event-requested",
                invocation=requested,
                event_type=EVENT_REQUESTED,
                payload={"phase": "requested"},
            ),
        ),
        organization_id=ORG_ID,
        capability_decision=_allow_decision(),
    )
    assert (
        await store.find_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=requested.idempotency_key,
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_committed_requested_row_makes_retry_in_progress_without_duplicate_work(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    handler_calls: list[str] = []

    async def counting_search(
        args: SearchInput, context: ToolCallContext
    ) -> dict[str, Any]:
        handler_calls.append("called")
        return {"hits": 1}

    crashing_store = CrashBeforeTerminalPersistStore(store)
    boundary = _boundary(crashing_store, handler=counting_search)
    kwargs = _call_kwargs(grants=_grants(), idempotency_key="crash-retry-key")

    with pytest.raises(RuntimeError, match="simulated crash"):
        await boundary.invoke(**kwargs)

    retry = await boundary.invoke(**kwargs)

    assert retry.status is ToolOutcomeStatus.IN_PROGRESS
    assert retry.retry.value == "retriable"
    assert retry.invocation.output is None
    assert handler_calls == ["called"]

    async with session_factory() as session:
        invocations = list((await session.scalars(select(AgentToolInvocation))).all())
        events = list((await session.scalars(select(AgentRunEvent))).all())

    assert len(invocations) == 1
    assert retry.invocation.tool_invocation_id == invocations[0].tool_invocation_id
    assert invocations[0].status == ToolInvocationStatus.REQUESTED.value
    assert [event.event_type for event in events] == [EVENT_REQUESTED]
    assert len({event.deduplication_key for event in events}) == 1

    terminal = _requested_invocation(status=ToolInvocationStatus.SUCCEEDED)
    await store.persist(
        invocation=terminal,
        events=(
            _event(
                event_id="replay-event-completed",
                invocation=terminal,
                event_type=EVENT_COMPLETED,
                payload={"phase": "completed"},
            ),
        ),
        organization_id=ORG_ID,
        capability_decision=_allow_decision(),
    )

    found = await store.find_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=terminal.idempotency_key,
    )
    assert found == terminal
    assert (
        await store.find_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=OTHER_ORG_ID,
            idempotency_key=terminal.idempotency_key,
        )
        is None
    )


@pytest.mark.asyncio
async def test_find_invocation_requires_organization_context(
    store: SessionToolAuditStore,
) -> None:
    with pytest.raises(MissingOrganizationContextError, match="organization"):
        await store.find_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=None,
            idempotency_key="missing-org-key",
        )


@pytest.mark.asyncio
async def test_the_store_is_not_installed_by_default() -> None:
    boundary = build_tool_boundary()

    assert isinstance(boundary._audit_store, InMemoryToolAuditStore)


@pytest.mark.asyncio
async def test_a_non_durable_identity_is_refused_by_the_durable_store(
    boundary: ToolBoundary,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identity = ToolCallIdentity.unbound(label="integration")
    assert identity.durable is False

    with pytest.raises(MissingOrganizationContextError, match="organization"):
        await boundary.invoke(
            **_call_kwargs(
                run_id=identity.run_id,
                task_id=identity.task_id,
                attempt_id=identity.attempt_id,
                organization_id=identity.organization_id,
            )
        )

    async with session_factory() as session:
        assert list((await session.scalars(select(AgentToolInvocation))).all()) == []
        assert list((await session.scalars(select(AgentRunEvent))).all()) == []
