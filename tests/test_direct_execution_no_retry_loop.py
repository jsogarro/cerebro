"""Regression test for the research-execution retry loop.

`ExecutionStatus.started_at` is timezone-aware (`datetime.now(UTC)`), but the
`finally` block in `_execute_research_workflow` set `completed_at` with a naive
`datetime.now()` and then subtracted the two. That raised

    TypeError: can't subtract offset-naive and offset-aware datetimes

from the `finally` block on *every* run. Because a `finally` raise propagates
even when the body succeeded, it tripped the `@retry` wrapper and re-ran the
entire workflow — so a query that had already completed was executed again,
leaving `GET /query/execution/{id}/status` pinned at `running / 40%` while the
pipeline churned through retries.

This test drives `_execute_research_workflow` with mocked collaborators and
asserts the workflow runs exactly once and reaches a terminal `completed` state
with a computed duration. On the pre-fix code the `finally` raises, `@retry`
re-runs it three times, and the call ultimately raises `RetryError`.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)


@dataclass
class _AllocStub:
    supervisor_type: str = "research"


@dataclass
class _ComplexityAnalysisStub:
    """Minimal complexity analysis stub for single-domain path."""

    decomposition: None = None  # No decomposition = single-domain path


@dataclass
class _DecisionStub:
    """Minimal dataclass so ``asdict(routing_decision)`` works."""

    agent_allocation: _AllocStub
    complexity_analysis: _ComplexityAnalysisStub


def _make_service() -> DirectExecutionService:
    router = MagicMock()
    router.route = AsyncMock(
        return_value=_DecisionStub(_AllocStub(), _ComplexityAnalysisStub()),
    )

    bridge = MagicMock()
    bridge.execute_routing_decision = AsyncMock(
        return_value=SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            agent_result=SimpleNamespace(output={"result": "ok"}, confidence=0.9),
            quality_score=0.8,
            consensus_score=0.7,
            workers_used=3,
            errors=[],
        ),
    )

    # event_publisher=None makes _publish_progress_update a no-op.
    return DirectExecutionService(
        masr_router=router, supervisor_bridge=bridge, event_publisher=None,
    )


def _make_project() -> MagicMock:
    project = MagicMock()
    project.id = uuid4()
    project.query.text = "compare two studies on sleep and memory"
    project.query.domains = ["research"]
    project.query.model_dump.return_value = {}
    project.scope.model_dump.return_value = {}
    project.user_id = "user-1"
    project.title = "Test project"
    return project


async def test_workflow_runs_once_and_reaches_completed() -> None:
    service = _make_service()
    project = _make_project()
    status = ExecutionStatus(
        execution_id="exec-1",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )

    # On the pre-fix code the finally raises TypeError, @retry re-runs the
    # workflow, and this await ultimately raises tenacity.RetryError.
    await service._execute_research_workflow(project, status, {})

    # Exactly one execution — no spurious retry.
    assert service.supervisor_bridge.execute_routing_decision.await_count == 1
    # Terminal state, correctly recorded.
    assert status.status == "completed"
    assert status.progress_percentage == 100.0
    # The finally block's duration math ran without raising.
    assert status.completed_at is not None
    assert status.execution_time_seconds >= 0.0
    # started_at/completed_at must agree on tz-awareness for the subtraction.
    assert status.started_at.tzinfo is not None
    assert status.completed_at.tzinfo is not None
