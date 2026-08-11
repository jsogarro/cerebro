"""A run is admitted once, no matter how the duplicate arrives.

Packet 3G's D3 fix coalesced a *sequential* replay of an admission. These
tests cover the two shapes it did not: two identical admissions arriving
concurrently (the ordinary form of a client network retry), and a replay of a
run whose outcome is already durably recorded — after a restart, or after the
in-process index that remembered it was evicted.

The bar every test here enforces is the wave's: a caller is never told an
outcome the database does not hold, and re-admitting a run never re-runs its
work.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    RunAdmissionError,
)
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import RunStatus
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import (
    ORG_ID,
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

pytestmark = [pytest.mark.integration]


class _CountingBridge:
    """A supervisor bridge that counts admissions and executions.

    Double execution is the defect under test, so the count — not a mock
    call assertion buried in a helper — is the thing each test reads.
    """

    def __init__(self, output: dict[str, Any] | None = None) -> None:
        self.admit_calls = 0
        self.execute_calls = 0
        self._output = output or {"result": "the real answer"}

    def admit_execution_plan(self, _plan: Any) -> None:
        self.admit_calls += 1

    async def execute_execution_plan(self, *_args: Any, **_kwargs: Any) -> Any:
        self.execute_calls += 1
        # A real supervisor yields; without this the "concurrent" admissions
        # would be serialized by the event loop and prove nothing.
        await asyncio.sleep(0)
        return Mock(output=dict(self._output), workers_used=1)


def _router() -> AsyncMock:
    router = AsyncMock()

    async def _route(*_args: Any, **_kwargs: Any) -> _RoutingDecisionStub:
        await asyncio.sleep(0)
        return _RoutingDecisionStub()

    router.route.side_effect = _route
    return router


def _service(
    session_factory: Any, resolver: Any, bridge: Any
) -> DirectExecutionService:
    return DirectExecutionService(
        masr_router=_router(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )


async def _await_settled(service: DirectExecutionService, execution_id: str) -> Any:
    """Wait until the workflow has fully finished with an execution.

    ``completed_at`` is set in ``_execute_research_workflow``'s ``finally``,
    after the terminal status flip and after an intervening checkpoint await
    — so a terminal status alone does not mean the workflow is done, and a
    test that acts on that gap (``cleanup_completed_executions`` needs
    ``completed_at``) races it.
    """
    for _ in range(200):
        execution = service.active_executions[execution_id]
        if (
            execution.status in ("completed", "failed", "cancelled")
            and execution.completed_at is not None
        ):
            return execution
        await asyncio.sleep(0.02)
    raise AssertionError(f"execution {execution_id} never settled")


@pytest.mark.asyncio
async def test_concurrent_identical_admissions_execute_the_run_once(
    test_engine: AsyncEngine,
) -> None:
    """Two identical admissions racing each other must produce one execution,
    one durable run, and one caller-visible outcome that the database
    actually holds."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "concurrent-admission-run"
    binding = make_binding(run_id, authority_id="concurrent-admission-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("concurrent-admission-authority", "1"): binding}
    )
    bridge = _CountingBridge()
    service = _service(session_factory, resolver, bridge)
    reference = ExecutionAuthorityReference(
        authority_id="concurrent-admission-authority", authority_version="1"
    )
    project = make_project()

    first, second = await asyncio.gather(
        service.start_research_execution(
            project, authority_reference=reference, organization_id=ORG_ID
        ),
        service.start_research_execution(
            project, authority_reference=reference, organization_id=ORG_ID
        ),
    )

    assert first == second
    assert bridge.admit_calls == 1

    execution = await _await_settled(service, first)
    assert bridge.execute_calls == 1
    assert execution.status == "completed"
    assert execution.run_id == run_id

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        # The status the caller was given is the status the database holds —
        # not a memory-only claim about a run no row backs.
        assert row is not None
        assert row.status == RunStatus.SUCCEEDED.value

    await service.close()


@pytest.mark.asyncio
async def test_replaying_a_terminal_run_after_restart_returns_the_recorded_outcome(
    test_engine: AsyncEngine,
) -> None:
    """A fresh process replaying an admission whose run already finished must
    hand back that run's recorded outcome, not run the workflow again."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "terminal-replay-run"
    binding = make_binding(run_id, authority_id="terminal-replay-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("terminal-replay-authority", "1"): binding}
    )
    reference = ExecutionAuthorityReference(
        authority_id="terminal-replay-authority", authority_version="1"
    )
    project = make_project()

    bridge_a = _CountingBridge({"result": "the first answer"})
    service_a = _service(session_factory, resolver, bridge_a)
    execution_id_a = await service_a.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )
    execution_a = await _await_settled(service_a, execution_id_a)
    assert execution_a.status == "completed"
    await service_a.close()

    # The process disappears; a fresh one shares only Postgres. The replayed
    # admission arrives at it.
    bridge_b = _CountingBridge({"result": "a second answer that must never exist"})
    service_b = _service(session_factory, resolver, bridge_b)
    execution_id_b = await service_b.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )

    assert bridge_b.admit_calls == 0
    assert bridge_b.execute_calls == 0
    # The durable record carries the original execution identity, so the
    # replaying caller gets the same handle it had before the restart.
    assert execution_id_b == execution_id_a

    replayed = await service_b.get_execution_status(
        execution_id_b, organization_id=ORG_ID
    )
    assert replayed is not None
    assert replayed.status == "completed"
    assert replayed.run_id == run_id
    assert replayed.final_output == {"result": "the first answer"}

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.SUCCEEDED.value

    await service_b.close()


@pytest.mark.asyncio
async def test_an_unreadable_database_refuses_admission_rather_than_guessing(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the durable record cannot be read, this process cannot know whether
    the run was already admitted. Admitting it on that assumption is exactly
    the double execution the durable check exists to prevent, so admission
    fails closed instead."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "unreadable-lookup-run"
    binding = make_binding(run_id, authority_id="unreadable-lookup-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("unreadable-lookup-authority", "1"): binding}
    )
    reference = ExecutionAuthorityReference(
        authority_id="unreadable-lookup-authority", authority_version="1"
    )
    project = make_project()

    bridge_a = _CountingBridge()
    service_a = _service(session_factory, resolver, bridge_a)
    execution_id = await service_a.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )
    await _await_settled(service_a, execution_id)
    await service_a.close()

    # A fresh process — nothing in memory — whose reads fail. The run really
    # is out there; this process just cannot see it.
    async def _unreadable(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("simulated read failure")

    monkeypatch.setattr(
        "src.repositories.run_lifecycle_repository.RunLifecycleRepository.get_run",
        _unreadable,
    )
    bridge_b = _CountingBridge()
    service_b = _service(session_factory, resolver, bridge_b)

    with pytest.raises(RunAdmissionError):
        await service_b.start_research_execution(
            project, authority_reference=reference, organization_id=ORG_ID
        )

    assert bridge_b.admit_calls == 0
    assert bridge_b.execute_calls == 0
    assert service_b.active_executions == {}

    await service_b.close()


@pytest.mark.asyncio
async def test_cross_process_duplicate_admission_coalesces_instead_of_raising(
    test_engine: AsyncEngine,
) -> None:
    """Reproduces N2: two ``DirectExecutionService`` instances — standing in
    for two replicas behind a load balancer — race to admit the exact same
    run. ``_admissions_in_flight`` only coalesces duplicates *within one
    process*, so both instances pass their own in-memory checks and race
    each other's durable ``create_run`` INSERT. The loser must hit Postgres'
    ``uq_agent_run_tenant_idempotency`` constraint and, instead of
    surfacing that as an admission failure (a 500 on an ordinary client
    retry that happened to land on a different replica), must coalesce onto
    the winner's execution — the same outcome an in-process concurrent
    duplicate already gets.
    """
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "cross-process-admission-run"
    binding = make_binding(run_id, authority_id="cross-process-admission-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("cross-process-admission-authority", "1"): binding}
    )
    reference = ExecutionAuthorityReference(
        authority_id="cross-process-admission-authority", authority_version="1"
    )
    project = make_project()

    bridge_a = _CountingBridge({"result": "answer from replica a"})
    bridge_b = _CountingBridge({"result": "answer from replica b"})
    service_a = _service(session_factory, resolver, bridge_a)
    service_b = _service(session_factory, resolver, bridge_b)

    results = await asyncio.gather(
        service_a.start_research_execution(
            project, authority_reference=reference, organization_id=ORG_ID
        ),
        service_b.start_research_execution(
            project, authority_reference=reference, organization_id=ORG_ID
        ),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, BaseException):
            raise result

    execution_id_a, execution_id_b = results
    assert isinstance(execution_id_a, str)
    assert isinstance(execution_id_b, str)
    # Both callers must see the same execution — one run was admitted, not
    # two — no matter which replica's write actually landed first.
    assert execution_id_a == execution_id_b

    # Poll the durable record rather than either service's in-memory
    # ``active_executions``: the loser's entry (registered by
    # ``_durably_recorded_execution_id`` when it coalesces) is a rehydrated
    # snapshot nothing ever updates again, so waiting on it would spin
    # forever whenever the loser happens to be the service this test polls.
    for _ in range(200):
        async with session_factory() as session:
            row = await RunLifecycleRepository(session).get_run(
                run_id, organization_id=ORG_ID
            )
        if row is not None and row.status in (
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
        ):
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError(f"run {run_id} never reached a terminal status")

    # Exactly one durable row for this run, and it reached its terminal
    # status — the durable record is the one thing every replica must agree
    # on, no matter which replica's write actually landed first.
    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.SUCCEEDED.value

    # Only the winner's supervisor bridge ever dispatches real work; the
    # loser's plan is validated (harmless) but never executed a second time.
    assert bridge_a.execute_calls + bridge_b.execute_calls == 1

    await service_a.close()
    await service_b.close()


@pytest.mark.asyncio
async def test_replay_after_the_in_process_index_is_evicted_does_not_re_execute(
    test_engine: AsyncEngine,
) -> None:
    """The same hole opens without a restart: ``cleanup_completed_executions``
    evicts a finished execution after 24h, and the replay index forgets it.
    The durable record must still answer for the run."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "evicted-replay-run"
    binding = make_binding(run_id, authority_id="evicted-replay-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("evicted-replay-authority", "1"): binding}
    )
    reference = ExecutionAuthorityReference(
        authority_id="evicted-replay-authority", authority_version="1"
    )
    project = make_project()

    bridge = _CountingBridge({"result": "the only answer"})
    service = _service(session_factory, resolver, bridge)
    execution_id = await service.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )
    await _await_settled(service, execution_id)

    assert await service.cleanup_completed_executions(max_age_hours=0) == 1
    assert execution_id not in service.active_executions

    replayed_execution_id = await service.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )

    assert bridge.admit_calls == 1
    assert bridge.execute_calls == 1
    assert replayed_execution_id == execution_id

    replayed = await service.get_execution_status(
        replayed_execution_id, organization_id=ORG_ID
    )
    assert replayed is not None
    assert replayed.status == "completed"
    assert replayed.final_output == {"result": "the only answer"}

    await service.close()
