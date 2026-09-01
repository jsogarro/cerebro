"""Integration coverage for Wave 3 Packet 3C: run/task/attempt/event persistence.

Proves ``DirectExecutionService`` writes through the durable schema (3A) via
3B's repositories at each material transition — admission, start, terminal
success, terminal failure, and cancellation — and that each transition's
event lands in the same transaction as its outbox row.
"""

import asyncio
import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from tenacity import wait_none

from src.ai_brain.router.routing_types import (
    RoutingExecutionPolicy,
    RoutingStrategy,
)
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    DurableWriteOutcome,
    ExecutionStatus,
)
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.claims import build_inventory
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupportStatus,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.core.contracts.states import (
    TERMINAL_ATTEMPT_STATUSES,
    TERMINAL_RUN_STATUSES,
    AttemptStatus,
    RunStatus,
    TaskStatus,
)
from src.models.db.claim_support import AgentClaimSupport
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000cd"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


@dataclass
class _AmbiguousCommitState:
    raise_after_next_commit: bool = False
    raised_count: int = 0


class _AmbiguousCommitSession(AsyncSession):
    """Delegate a commit fully, then inject one ambiguous outcome."""

    def __init__(
        self, *args: Any, commit_state: _AmbiguousCommitState, **kwargs: Any
    ) -> None:
        self._commit_state = commit_state
        super().__init__(*args, **kwargs)

    async def commit(self) -> None:
        await super().commit()
        if self._commit_state.raise_after_next_commit:
            self._commit_state.raise_after_next_commit = False
            self._commit_state.raised_count += 1
            raise RuntimeError("ambiguous commit result")


# Mirrors ``tests/test_direct_execution_service.py``'s ``_FakeRoutingDecision``
# family: the smallest dataclass shape ``ExecutionPlanCompiler.compile`` and
# ``asdict(routing_decision)`` both accept, matched to ``_make_binding``'s
# ``strategy="balanced"``/``collaboration_mode="hierarchical"``.
@dataclass
class _AllocStub:
    supervisor_type: str = "research"
    worker_count: int = 1
    worker_types: list[str] = field(default_factory=lambda: ["literature"])


@dataclass
class _ComplexityAnalysisStub:
    domains: list[str] = field(default_factory=lambda: ["research"])
    decomposition: None = None


@dataclass
class _RoutingDecisionStub:
    query_id: str = "test-query"
    collaboration_mode: str = "hierarchical"
    agent_allocation: _AllocStub = field(default_factory=_AllocStub)
    complexity_analysis: _ComplexityAnalysisStub = field(
        default_factory=_ComplexityAnalysisStub
    )
    estimated_cost: float = 0.01
    estimated_latency_ms: int = 1000
    estimated_quality: float = 0.9
    confidence_score: float = 0.9
    context: dict[str, Any] = field(default_factory=dict)
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    optimization_result: Any = field(
        default_factory=lambda: type(
            "_Optimization",
            (),
            {
                "primary_model": type(
                    "_Model",
                    (),
                    {"provider": "gemini", "model_name": "gemini-2.5-pro"},
                )(),
                "fallback_models": [],
            },
        )()
    )


def _make_binding(run_id: str) -> ExecutionAuthorityBinding:
    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id="persistence-authority",
        authority_version="1",
        run_id=run_id,
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=(
            WorkerAssignment(
                worker_id="worker-1",
                worker_type="literature",
                objective="Find sources",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
        edges=(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id="worker-1",
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=0,
            max_total_tokens=1,
            max_tool_invocations=0,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )
    # ``create_for_test`` hardcodes ``tenant_id="tenant-1"``, which cannot be
    # persisted — the accepted Wave 3 tenant identity decision requires
    # ``tenant_id`` to round-trip through ``uuid.UUID(...)``. Also fix up the
    # idempotency key so distinct runs in the same test module don't collide
    # on ``(tenant_id, idempotency_key)``.
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={"tenant_id": ORG_ID, "idempotency_key": f"key-{run_id}"}
        ),
    )


def _project(project_id: Any = None) -> ResearchProject:
    kwargs: dict[str, Any] = {
        "title": "Restart durability test",
        "query": ResearchQuery(
            text="What changed in the durable run lifecycle?",
            domains=["research"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        "user_id": "user-1",
        "scope": ResearchScope(max_sources=5),
    }
    if project_id is not None:
        kwargs["id"] = project_id
    return ResearchProject(**kwargs)


def _make_service(
    *, session_factory: Any, resolver: MappingExecutionAuthorityResolver
) -> DirectExecutionService:
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()

    return DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_fixture_mode_run_persists_to_succeeded_with_journaled_result(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "persist-run-succeeded"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    project = _project()
    fixture_payload = {"summary": "synthetic result", "sources": ["s1"]}

    execution_id = await service.start_research_execution(
        project,
        execution_policy=RoutingExecutionPolicy.fixture(),
        fixture_result=fixture_payload,
        authority_reference=ExecutionAuthorityReference(
            authority_id="persistence-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    for _ in range(50):
        status = service.active_executions[execution_id]
        if status.status == "completed":
            break
        await asyncio.sleep(0.02)
    assert service.active_executions[execution_id].status == "completed"

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        assert run_row.status == "succeeded"
        assert run_row.status in {s.value for s in TERMINAL_RUN_STATUSES}

        tasks = await lifecycle_repo.get_tasks_for_run(run_id, organization_id=ORG_ID)
        assert len(tasks) == 1
        task_row = tasks[0]
        assert task_row.status == "succeeded"
        assert task_row.task_key == execution_id
        assert task_row.input["project_id"] == str(project.id)

        attempts = await lifecycle_repo.get_attempts_for_task(
            task_row.task_id, organization_id=ORG_ID
        )
        assert len(attempts) == 1
        attempt_row = attempts[0]
        assert attempt_row.status == "succeeded"
        assert attempt_row.status in {s.value for s in TERMINAL_ATTEMPT_STATUSES}
        assert attempt_row.journaled_result is not None
        assert attempt_row.journaled_result["final_output"] == fixture_payload
        assert attempt_row.journaled_result["agent_results"] == fixture_payload

        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )
        event_types = [event.event_type for event in events]
        # admitted -> started -> succeeded, strictly ordered by sequence.
        assert event_types == ["run.admitted", "run.started", "run.succeeded"]
        assert [event.sequence for event in events] == [1, 2, 3]

        from sqlalchemy import select

        from src.models.db.run_event import AgentRunEventOutbox

        outbox_rows = (
            (
                await session.execute(
                    select(AgentRunEventOutbox).where(
                        AgentRunEventOutbox.run_id == run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(outbox_rows) == 3
        assert all(row.destination == "redis" for row in outbox_rows)
        assert all(row.status == "pending" for row in outbox_rows)
        # Every outbox row references an event that was committed in the same
        # transaction — none of them can be an orphan.
        assert {row.event_id for row in outbox_rows} == {
            event.event_id for event in events
        }

    await service.close()


@pytest.mark.asyncio
async def test_successful_fixture_run_records_content_bound_report_claims(
    test_engine: AsyncEngine,
) -> None:
    """A successful run records one complete claim resolution for its report."""
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "persist-run-final-report"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    project = _project()
    fixture_payload = {
        "report": (
            "The protocol reduces median latency by twenty percent. "
            "The protocol improves reliability across three regions."
        ),
        "sources": ["s1"],
    }

    execution_id = await service.start_research_execution(
        project,
        execution_policy=RoutingExecutionPolicy.fixture(),
        fixture_result=fixture_payload,
        authority_reference=ExecutionAuthorityReference(
            authority_id="persistence-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    for _ in range(100):
        if service.active_executions[execution_id].status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)
    assert service.active_executions[execution_id].status == "completed"

    report_text = json.dumps(
        fixture_payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    report_digest = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        task_rows = await lifecycle_repo.get_tasks_for_run(
            run_id, organization_id=ORG_ID
        )
        assert len(task_rows) == 1
        task_row = task_rows[0]
        attempt_rows = await lifecycle_repo.get_attempts_for_task(
            task_row.task_id, organization_id=ORG_ID
        )
        assert len(attempt_rows) == 1
        attempt_row = attempt_rows[0]

        artifacts = await ArtifactRepository(session).list_artifacts_for_run(
            run_id, organization_id=ORG_ID
        )
        supports = (
            (
                await session.execute(
                    select(AgentClaimSupport)
                    .where(AgentClaimSupport.run_id == run_id)
                    .order_by(AgentClaimSupport.claim_id)
                )
            )
            .scalars()
            .all()
        )

    # RED on the pre-D1 implementation: both collections are empty because
    # the terminal path has no report/claim write at all.
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.organization_id is not None
    assert str(artifact.organization_id) == ORG_ID
    assert artifact.run_id == run_id
    assert artifact.task_id == task_row.task_id
    assert artifact.attempt_id == attempt_row.attempt_id
    assert artifact.kind == "research_report"
    assert artifact.media_type == "application/json"
    assert artifact.storage_uri == f"memory://artifacts/{artifact.artifact_id}"
    assert artifact.content_sha256 == report_digest
    assert artifact.metadata_["content"] == report_text
    assert artifact.metadata_["serialization"] == "canonical_json"
    assert artifact.metadata_["content_encoding"] == "utf-8"
    assert artifact.metadata_["source"] == "final_output"

    inventory = build_inventory(
        artifact_id=artifact.artifact_id,
        artifact_kind=artifact.kind,
        source=artifact.metadata_["content"],
    )
    assert inventory.material_claim_count >= 2
    assert len(supports) == inventory.material_claim_count
    supports_by_claim = {support.claim_id: support for support in supports}
    assert set(supports_by_claim) == {claim.claim_id for claim in inventory.claims}
    for claim in inventory.claims:
        support = supports_by_claim[claim.claim_id]
        assert support.organization_id is not None
        assert str(support.organization_id) == ORG_ID
        assert support.run_id == run_id
        assert support.artifact_id == artifact.artifact_id
        assert support.claim_text == claim.text
        assert support.status == ClaimSupportStatus.UNSUPPORTED.value
        assert (
            support.absent_evidence_reason == AbsentEvidenceReason.NOT_ATTEMPTED.value
        )

    await service.close()


@pytest.mark.asyncio
async def test_terminal_transition_reconciles_after_ambiguous_commit(
    test_engine: AsyncEngine,
) -> None:
    """A post-commit error is reconciled without duplicating terminal rows."""
    commit_state = _AmbiguousCommitState()
    session_factory = sessionmaker(
        test_engine,
        class_=_AmbiguousCommitSession,
        expire_on_commit=False,
        commit_state=commit_state,
    )
    run_id = "persist-run-ambiguous-terminal"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    service.durable_write_max_attempts = 2
    service.durable_write_retry_seconds = 0
    service.durable_reconcile_interval_seconds = 60.0
    project = _project()
    fixture_payload = {
        "report": (
            "The protocol reduces median latency by twenty percent. "
            "The protocol improves reliability across three regions."
        ),
        "sources": ["s1"],
    }
    execution_status = ExecutionStatus(
        execution_id="exec-ambiguous-terminal",
        project_id=str(project.id),
        status="pending",
    )

    await service._admit_run(execution_status, binding, project)
    assert (
        await service._persist_transition(
            execution_status,
            run_target=RunStatus.RUNNING,
            task_target=TaskStatus.RUNNING,
            attempt_target=AttemptStatus.RUNNING,
            event_type="run.started",
            payload={},
        )
        is DurableWriteOutcome.RECORDED
    )

    execution_status.final_output = fixture_payload
    execution_status.agent_results = fixture_payload
    commit_state.raise_after_next_commit = True
    terminal_outcome = await service._persist_transition(
        execution_status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={"fixture_mode": True},
    )
    service._settle_terminal_state(
        execution_status,
        outcome=terminal_outcome,
        status="completed",
        phase="completed",
        progress=100.0,
    )
    await service.close()

    assert commit_state.raised_count == 1
    assert terminal_outcome is DurableWriteOutcome.RECORDED
    assert execution_status.status == "completed"
    assert execution_status.unconfirmed_terminal_status is None
    assert service._pending_terminal_outcomes == {}

    report_text = json.dumps(
        fixture_payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    report_digest = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        assert run_row.status == RunStatus.SUCCEEDED.value
        task_rows = await lifecycle_repo.get_tasks_for_run(
            run_id, organization_id=ORG_ID
        )
        assert len(task_rows) == 1
        task_row = task_rows[0]
        attempt_rows = await lifecycle_repo.get_attempts_for_task(
            task_row.task_id, organization_id=ORG_ID
        )
        assert len(attempt_rows) == 1
        attempt_row = attempt_rows[0]
        assert attempt_row.status == AttemptStatus.SUCCEEDED.value

        artifacts = await ArtifactRepository(session).list_artifacts_for_run(
            run_id, organization_id=ORG_ID
        )
        supports = (
            (
                await session.execute(
                    select(AgentClaimSupport).where(AgentClaimSupport.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.organization_id is not None
    assert str(artifact.organization_id) == ORG_ID
    assert artifact.run_id == run_id
    assert artifact.task_id == task_row.task_id
    assert artifact.attempt_id == attempt_row.attempt_id
    assert artifact.content_sha256 == report_digest
    assert artifact.metadata_["content"] == report_text

    inventory = build_inventory(
        artifact_id=artifact.artifact_id,
        artifact_kind=artifact.kind,
        source=report_text,
    )
    assert len(supports) == inventory.material_claim_count
    supports_by_claim = {support.claim_id: support for support in supports}
    assert set(supports_by_claim) == {claim.claim_id for claim in inventory.claims}
    for claim in inventory.claims:
        support = supports_by_claim[claim.claim_id]
        assert support.organization_id is not None
        assert str(support.organization_id) == ORG_ID
        assert support.run_id == run_id
        assert support.artifact_id == artifact.artifact_id
        assert support.claim_text == claim.text

    assert [event.event_type for event in events] == [
        "run.admitted",
        "run.started",
        "run.succeeded",
    ]
    terminal_events = [event for event in events if event.event_type == "run.succeeded"]
    assert len(terminal_events) == 1
    assert terminal_events[0].event_id == terminal_events[0].deduplication_key


@pytest.mark.asyncio
async def test_nonterminal_transition_reconciles_after_ambiguous_commit(
    test_engine: AsyncEngine,
) -> None:
    """A post-commit error does not duplicate a non-terminal transition."""
    commit_state = _AmbiguousCommitState()
    session_factory = sessionmaker(
        test_engine,
        class_=_AmbiguousCommitSession,
        expire_on_commit=False,
        commit_state=commit_state,
    )
    run_id = "persist-run-ambiguous-started"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    service.durable_write_max_attempts = 2
    service.durable_write_retry_seconds = 0
    project = _project()
    execution_status = ExecutionStatus(
        execution_id="exec-ambiguous-started",
        project_id=str(project.id),
        status="pending",
    )

    await service._admit_run(execution_status, binding, project)
    commit_state.raise_after_next_commit = True
    outcome = await service._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    await service.close()

    assert commit_state.raised_count == 1
    assert outcome is DurableWriteOutcome.RECORDED

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        assert run_row.status == RunStatus.RUNNING.value
        task_rows = await lifecycle_repo.get_tasks_for_run(
            run_id, organization_id=ORG_ID
        )
        assert len(task_rows) == 1
        assert task_rows[0].status == TaskStatus.RUNNING.value
        attempt_rows = await lifecycle_repo.get_attempts_for_task(
            task_rows[0].task_id, organization_id=ORG_ID
        )
        assert len(attempt_rows) == 1
        assert attempt_rows[0].status == AttemptStatus.RUNNING.value
        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )

    assert [event.event_type for event in events] == [
        "run.admitted",
        "run.started",
    ]
    started_events = [event for event in events if event.event_type == "run.started"]
    assert len(started_events) == 1
    assert started_events[0].event_id == started_events[0].deduplication_key
    assert len({event.deduplication_key for event in events}) == len(events)


@pytest.mark.asyncio
async def test_final_report_and_claims_roll_back_with_terminal_transition(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after a support flush leaves no terminal or partial rows."""
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "persist-run-final-report-rollback"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    service.durable_write_max_attempts = 1
    service.durable_reconcile_interval_seconds = 60.0
    project = _project()
    fixture_payload = {
        "report": (
            "The protocol reduces median latency by twenty percent. "
            "The protocol improves reliability across three regions."
        )
    }
    original_record = ClaimSupportRepository.record_claim_support
    record_calls = 0

    async def record_then_fail(
        repository: ClaimSupportRepository,
        claim: Any,
        *,
        organization_id: Any,
    ) -> Any:
        nonlocal record_calls
        record_calls += 1
        row = await original_record(repository, claim, organization_id=organization_id)
        if record_calls >= 2:
            raise RuntimeError("simulated claim-support recorder failure")
        return row

    monkeypatch.setattr(
        ClaimSupportRepository, "record_claim_support", record_then_fail
    )

    execution_id = await service.start_research_execution(
        project,
        execution_policy=RoutingExecutionPolicy.fixture(),
        fixture_result=fixture_payload,
        authority_reference=ExecutionAuthorityReference(
            authority_id="persistence-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    for _ in range(100):
        execution = service.active_executions[execution_id]
        if execution.unconfirmed_terminal_status == "completed":
            break
        await asyncio.sleep(0.02)

    execution = service.active_executions[execution_id]
    assert record_calls >= 2
    assert execution.status == "running"
    assert execution.unconfirmed_terminal_status == "completed"

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        assert run_row.status == "running"
        task_rows = await lifecycle_repo.get_tasks_for_run(
            run_id, organization_id=ORG_ID
        )
        assert len(task_rows) == 1
        assert task_rows[0].status == "running"
        attempt_rows = await lifecycle_repo.get_attempts_for_task(
            task_rows[0].task_id, organization_id=ORG_ID
        )
        assert len(attempt_rows) == 1
        assert attempt_rows[0].status == "running"

        artifacts = await ArtifactRepository(session).list_artifacts_for_run(
            run_id, organization_id=ORG_ID
        )
        supports = (
            (
                await session.execute(
                    select(AgentClaimSupport).where(AgentClaimSupport.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )

    assert artifacts == []
    assert supports == []
    assert [event.event_type for event in events] == [
        "run.admitted",
        "run.started",
    ]

    await service.close()


@pytest.mark.asyncio
async def test_fixture_execution_without_session_factory_stays_memory_only() -> None:
    """D1 persistence must not make the existing no-session path required."""
    run_id = "persist-run-memory-only"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=None, resolver=resolver)
    project = _project()
    fixture_payload = {
        "report": "The memory-only path continues to return its fixture result."
    }

    execution_id = await service.start_research_execution(
        project,
        execution_policy=RoutingExecutionPolicy.fixture(),
        fixture_result=fixture_payload,
        authority_reference=ExecutionAuthorityReference(
            authority_id="persistence-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    for _ in range(100):
        if service.active_executions[execution_id].status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)

    execution = service.active_executions[execution_id]
    assert execution.status == "completed"
    assert execution.final_output == fixture_payload
    assert execution.run_id is None

    await service.close()


@pytest.mark.parametrize(
    "final_output",
    [None, {"report": object()}],
    ids=["absent", "unserializable"],
)
@pytest.mark.asyncio
async def test_successful_transition_rejects_an_unreadable_final_output(
    test_engine: AsyncEngine, final_output: Any
) -> None:
    """An unreadable result cannot create a successful report or transition."""
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "persist-run-unreadable-final-report"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    project = _project()
    execution_status = ExecutionStatus(
        execution_id="exec-unreadable-final-report",
        project_id=str(project.id),
        status="pending",
    )

    await service._admit_run(execution_status, binding, project)
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    execution_status.final_output = final_output

    with pytest.raises(ValueError, match="final_output"):
        await service._persist_transition(
            execution_status,
            run_target=RunStatus.SUCCEEDED,
            task_target=TaskStatus.SUCCEEDED,
            attempt_target=AttemptStatus.SUCCEEDED,
            event_type="run.succeeded",
            payload={},
        )

    async with session_factory() as session:
        run_row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert run_row is not None
        assert run_row.status == RunStatus.RUNNING.value
        assert (
            await ArtifactRepository(session).list_artifacts_for_run(
                run_id, organization_id=ORG_ID
            )
            == []
        )
        supports = (
            (
                await session.execute(
                    select(AgentClaimSupport).where(AgentClaimSupport.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )

    assert supports == []
    assert [event.event_type for event in events] == [
        "run.admitted",
        "run.started",
    ]

    await service.close()


@pytest.mark.asyncio
async def test_execution_failure_persists_run_as_failed_with_reason(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose every attempt failed is durably FAILED, with the reason.

    This test used to poll for a "failed" row while retries were still in
    flight, on the premise that the first attempt's exception handler
    persisted the terminal failure immediately. That premise was the defect:
    a run marked terminally failed before its retries are exhausted is
    immutable, so a retry that then succeeded left the caller reading
    "completed" against a durable row that said "failed". The failure is now
    recorded once no attempt remains, so this waits for the run to actually
    finish failing.
    """
    monkeypatch.setattr(
        DirectExecutionService._execute_research_workflow.retry, "wait", wait_none()
    )
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "persist-run-failed"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)
    service.supervisor_bridge.execute_execution_plan.side_effect = RuntimeError(
        "supervisor exploded"
    )

    project = _project()
    await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="persistence-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    # Every attempt fails, so the run does end up FAILED — but only after
    # tenacity has spent them all. Poll the database rather than the
    # in-memory dict, which flips back to "running" at the top of each
    # retry; the backoff is patched out above so this stays fast.
    async def _run_failed() -> bool:
        async with session_factory() as poll_session:
            row = await RunLifecycleRepository(poll_session).get_run(
                run_id, organization_id=ORG_ID
            )
            return row is not None and row.status == "failed"

    for _ in range(50):
        if await _run_failed():
            break
        await asyncio.sleep(0.05)
    assert await _run_failed()

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        assert run_row.status == "failed"
        assert run_row.status_reason
        assert "supervisor exploded" in run_row.status_reason
        assert (
            await ArtifactRepository(session).list_artifacts_for_run(
                run_id, organization_id=ORG_ID
            )
            == []
        )
        supports = (
            (
                await session.execute(
                    select(AgentClaimSupport).where(AgentClaimSupport.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        assert supports == []

        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )
        assert events[-1].event_type == "run.failed"

    await service.close()


@pytest.mark.asyncio
async def test_cancellation_persists_run_as_cancelling_not_cancelled(
    test_engine: AsyncEngine,
) -> None:
    """Cancellation can only durably reach ``CANCELLING``.

    ``cancel_execution`` has no mechanism to observe the running
    ``asyncio.Task`` actually stop, so persisting ``CANCELLED`` would
    fabricate a terminal outcome for work that may still be in flight. This
    is the documented, intentional limit of what this packet's cancellation
    path can honestly record.
    """
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "persist-run-cancelling"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver(
        {("persistence-authority", "1"): binding}
    )
    service = _make_service(session_factory=session_factory, resolver=resolver)

    supervisor_started = asyncio.Event()
    release_supervisor = asyncio.Event()

    async def _blocking_execute_plan(*_args: Any, **_kwargs: Any) -> Any:
        supervisor_started.set()
        await release_supervisor.wait()
        return Mock(output={"result": "ok"}, workers_used=1)

    service.supervisor_bridge.execute_execution_plan.side_effect = (
        _blocking_execute_plan
    )

    project = _project()
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="persistence-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )
    await asyncio.wait_for(supervisor_started.wait(), timeout=5)

    cancelled = await service.cancel_execution(execution_id)
    assert cancelled is True

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert run_row is not None
        assert run_row.status == "cancelling"
        assert run_row.status not in {s.value for s in TERMINAL_RUN_STATUSES}
        assert run_row.cancellation_requested_at is not None
        assert (
            await ArtifactRepository(session).list_artifacts_for_run(
                run_id, organization_id=ORG_ID
            )
            == []
        )
        supports = (
            (
                await session.execute(
                    select(AgentClaimSupport).where(AgentClaimSupport.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        assert supports == []

        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )
        assert events[-1].event_type == "run.cancellation_requested"

    release_supervisor.set()
    await service.close()
