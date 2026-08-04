"""Unit coverage for the graceful-degradation posture of the new durable
persistence calls in ``DirectExecutionService`` (Wave 3 Packet 3C).

Mirrors the existing ``_checkpoint`` contract: execution must keep working
in memory-only mode when there is no session factory, when this execution
was never durably admitted (e.g. tests that construct ``ExecutionStatus``
directly), or when persistence itself fails. None of these should ever
raise out of the public surface.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus


def _service(*, session_factory=None) -> DirectExecutionService:
    return DirectExecutionService(
        masr_router=MagicMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=MagicMock(),
        session_factory=session_factory,
    )


def _status(execution_id: str = "exec-1") -> ExecutionStatus:
    return ExecutionStatus(
        execution_id=execution_id,
        project_id="00000000-0000-0000-0000-000000000001",
        status="pending",
    )


@pytest.mark.asyncio
async def test_persist_transition_noop_without_session_factory() -> None:
    service = _service(session_factory=None)
    status = _status()

    await service._persist_transition(
        status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )

    assert status._run is None


@pytest.mark.asyncio
async def test_persist_transition_noop_when_never_admitted() -> None:
    """A session factory is present, but this ``ExecutionStatus`` was never
    produced by ``start_research_execution`` (no ``._run``) — the shape many
    existing tests drive ``_execute_research_workflow`` with directly."""
    session_factory = MagicMock()
    service = _service(session_factory=session_factory)
    status = _status()
    assert status._run is None

    await service._persist_transition(
        status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )

    session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_persist_cancellation_noop_without_session_factory() -> None:
    service = _service(session_factory=None)
    status = _status()

    await service._persist_cancellation(status)

    assert status._run is None


@pytest.mark.asyncio
async def test_restore_active_executions_noop_without_session_factory() -> None:
    service = _service(session_factory=None)

    restored = await service.restore_active_executions()

    assert restored == 0
    assert service.active_executions == {}


@pytest.mark.asyncio
async def test_admit_run_failure_is_caught_and_does_not_raise() -> None:
    """A session factory that raises on use (e.g. an unreachable DB, or a
    non-UUID ``tenant_id`` tripping the tenant identity contract) must not
    propagate out of admission — execution continues in memory-only mode."""
    from src.models.execution_authority import ExecutionAuthorityBinding

    class _ExplodingSessionFactory:
        def __call__(self):
            raise RuntimeError("database unreachable")

    service = _service(session_factory=_ExplodingSessionFactory())
    status = _status()

    from datetime import timedelta

    from src.core.contracts import (
        ExecutionBudget,
        FallbackMode,
        ProviderModelPolicy,
        ProviderModelRoute,
        WorkerAssignment,
    )
    from src.models.research_project import (
        ResearchDepth,
        ResearchProject,
        ResearchQuery,
        ResearchScope,
    )

    now = datetime(2026, 8, 4, tzinfo=UTC)
    binding = ExecutionAuthorityBinding.create_for_test(
        authority_id="a",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id="w",
        routing_policy_id="p",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id=None,
        supervisor_type=None,
        workers=(
            WorkerAssignment(
                worker_id="w1",
                worker_type="literature",
                objective="x",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
        edges=(),
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
            task_timeout_seconds=1,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=5),
        compiled_at=now,
    )
    project = ResearchProject(
        title="t",
        query=ResearchQuery(
            text="q", domains=["research"], depth_level=ResearchDepth.COMPREHENSIVE
        ),
        user_id="u",
        scope=ResearchScope(max_sources=1),
    )

    # Must not raise.
    await service._admit_run(status, binding, project)

    assert status._run is None
    assert status.run_id is None
