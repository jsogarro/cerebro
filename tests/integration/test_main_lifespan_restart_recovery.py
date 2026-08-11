"""Proves ``src/api/main.py``'s actual lifespan wiring — not just the
primitives it calls — closes the Wave 3 Packet 3C restart hazard.

Packet 3B's own integration test already proves
``PersistedExecutionAuthorityResolver.warm_from_snapshots`` rebuilds a cache
after a restart, and this packet's
``tests/integration/test_direct_execution_restart_recovery.py`` proves
``DirectExecutionService.restore_active_executions`` rehydrates
``active_executions``. Neither proves ``src/api/main.py`` actually wires
those two calls into the real lifespan in the right order before the app
starts serving — that is what this test targets: durable state seeded
before "the process disappears" (module-level DB globals reset, so
``init_db()`` reinitializes as if from cold), read back by a brand-new
``FastAPI`` app entering ``main.lifespan`` for the first time, exactly as
happens on a real restart.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    PersistedExecutionAuthorityResolver,
)
from src.core.contracts import (
    Attempt,
    ExecutionBudget,
    FallbackMode,
    PinnedVersions,
    ProviderModelPolicy,
    ProviderModelRoute,
    RunStatus,
    Task,
    TaskStatus,
    WorkerAssignment,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000fe"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _seeded_binding(run_id: str) -> ExecutionAuthorityBinding:
    import dataclasses

    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id="lifespan-restart-authority",
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
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={"tenant_id": ORG_ID, "idempotency_key": f"key-{run_id}"}
        ),
    )


async def _seed_pre_restart_state(
    session_factory, *, run_id: str, execution_id: str, project_id: str
) -> None:
    """Write exactly the durable rows a real admitted-and-running execution
    would have left behind, using the same repositories production code
    uses — independent of any FastAPI app or lifespan."""

    binding = _seeded_binding(run_id)
    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        await lifecycle_repo.create_run(binding.run, organization_id=ORG_ID)
        queued = binding.run.transition_to(RunStatus.QUEUED, at=NOW)
        running = queued.transition_to(RunStatus.RUNNING, at=NOW)
        await lifecycle_repo.record_run_transition(running, organization_id=ORG_ID)

        task = Task(
            task_id=str(uuid.uuid4()),
            run_id=run_id,
            task_key=execution_id,
            task_type="execution_plan",
            objective="Restart recovery via the real lifespan",
            idempotency_key=f"{binding.run.idempotency_key}:task",
            input={"project_id": project_id, "execution_id": execution_id},
            created_at=NOW,
            updated_at=NOW,
        )
        await lifecycle_repo.create_task(task, organization_id=ORG_ID)
        ready = task.transition_to(TaskStatus.READY, at=NOW)
        running_task = ready.transition_to(TaskStatus.RUNNING, at=NOW)
        await lifecycle_repo.record_task_transition(
            running_task, organization_id=ORG_ID
        )

        attempt = Attempt(
            attempt_id=str(uuid.uuid4()),
            task_id=task.task_id,
            ordinal=1,
            idempotency_key=f"{task.idempotency_key}:attempt-1",
            created_at=NOW,
            updated_at=NOW,
        )
        await lifecycle_repo.create_attempt(
            attempt, run_id=run_id, organization_id=ORG_ID
        )

        resolver = PersistedExecutionAuthorityResolver(clock=lambda: NOW)
        await resolver.register(
            session,
            binding=binding,
            organization_id=ORG_ID,
            config_snapshot_id=f"snapshot-{run_id}",
            pinned_versions=PinnedVersions(
                workflow_definition_id="workflow-1",
                workflow_definition_version="1",
                routing_policy_id="policy-1",
                routing_policy_version="1",
                event_envelope_version="1.0",
            ),
        )
        await session.commit()


@pytest.mark.asyncio
async def test_main_lifespan_warms_authority_and_restores_active_executions(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api import main
    from src.api.services import (
        agent_execution_service,
        masr_routing_service,
        supervisor_coordination_service,
        talkhier_session_service,
    )
    from src.models.db import session as db_session_module

    # ``str(engine.url)`` masks the password; the real DSN is required for a
    # second, independent engine (main.lifespan's own ``init_db()``) to
    # authenticate against the same testcontainer.
    connection_url = test_engine.url.render_as_string(hide_password=False)
    run_id = "lifespan-restart-run"
    execution_id = "lifespan-restart-execution"
    project_id = str(uuid.uuid4())

    seed_session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    await _seed_pre_restart_state(
        seed_session_factory,
        run_id=run_id,
        execution_id=execution_id,
        project_id=project_id,
    )

    # Simulate "the process disappeared": reset the module-level engine/
    # session-factory globals so the next ``init_db()`` call inside
    # ``main.lifespan`` initializes fresh, the same way it would on a real
    # process restart — while pointing at the *same* database, so what it
    # finds there is exactly what a real restart would find.
    db_session_module._engine = None
    db_session_module._async_session_factory = None
    monkeypatch.setattr(main.settings, "DATABASE_URL", connection_url)

    class _Runtime:
        def __init__(self) -> None:
            self.router = SimpleNamespace(route=AsyncMock())

        async def close(self) -> None:
            return None

    class _LifespanService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def close(self) -> None:
            return None

    monkeypatch.setattr(main.MASRRuntime, "create", AsyncMock(return_value=_Runtime()))
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(main.event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(main.event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(
        agent_execution_service,
        "configure_agent_execution_service",
        lambda **_kwargs: AsyncMock(),
    )
    monkeypatch.setattr(masr_routing_service, "MASRRoutingService", _LifespanService)
    monkeypatch.setattr(
        talkhier_session_service, "TalkHierSessionService", _LifespanService
    )
    monkeypatch.setattr(
        supervisor_coordination_service,
        "SupervisorCoordinationService",
        _LifespanService,
    )

    test_app = FastAPI(lifespan=main.lifespan)
    # Deliberately do NOT set ``test_app.state.execution_authority_resolver``
    # — that override branch is what every other lifespan test exercises.
    # Leaving it unset forces the real
    # ``PersistedExecutionAuthorityResolver`` + ``warm_from_snapshots`` path.

    try:
        with TestClient(test_app) as client:  # noqa: F841 — triggers lifespan
            service = test_app.state.direct_execution_service
            assert isinstance(service, DirectExecutionService)
            resolver = service.execution_authority_resolver
            assert isinstance(resolver, PersistedExecutionAuthorityResolver)

            # ``resolve`` is synchronous and reads only the in-process cache
            # this assertion is safe to call directly (no coroutine, no
            # cross-event-loop DB access) — it is the same call production
            # code makes at authority-resolution time.
            resolved = resolver.resolve(
                ExecutionAuthorityReference(
                    authority_id="lifespan-restart-authority", authority_version="1"
                ),
                organization_id=ORG_ID,
            )
            assert resolved.run.run_id == run_id

            assert execution_id in service.active_executions
            recovered = service.active_executions[execution_id]
            assert recovered.status == "running"
            assert recovered.run_id == run_id
            assert recovered.project_id == project_id
    finally:
        db_session_module._engine = None
        db_session_module._async_session_factory = None
