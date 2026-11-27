"""Worker outcomes reach the durable run, task, attempt and event records alike.

Drives the real ``DirectExecutionService`` -> ``MASRSupervisorBridge`` -> content
workers path against PostgreSQL. Only the provider call is replaced, at
``ModelRouter.route_and_generate``, so everything above it is production code.
"""

import asyncio
import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

import src.ai_brain.integration.masr_supervisor_bridge as bridge_module
from src.ai_brain.providers import ModelRouter
from src.ai_brain.providers.base_provider import ModelResponse
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
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
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.test_direct_execution_run_persistence import (
    _AllocStub,
    _RoutingDecisionStub,
)

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000dd"
AUTHORITY_ID = "outcomes-authority"
# Distinct content workers, told apart by the persona each prompt opens with.
PERSONA = {
    "content_planning": "content plan",
    "drafting": "You are a writer",
    "editing": "You are an editor",
}
_NO_SCHEMA: dict[str, Any] = {}
_NEEDS_VERDICT = {"type": "object", "required": ["verdict"]}

Generate = Callable[[str], ModelResponse]


def _ok(prompt: str) -> ModelResponse:
    return ModelResponse(
        request_id="r",
        content="a usable result",
        success=True,
        confidence_score=0.9,
        total_tokens=10,
    )


def _fail(prompt: str) -> ModelResponse:
    # A provider that answers without content: the worker reports "failed".
    return ModelResponse(
        request_id="r",
        content=None,  # type: ignore[arg-type]
        success=True,
        total_tokens=10,
    )


def _worker(worker_type: str, schema: dict[str, Any] = _NO_SCHEMA) -> WorkerAssignment:
    return WorkerAssignment(
        worker_id=f"w-{worker_type}",
        worker_type=worker_type,
        objective="Do the work",
        output_schema=schema,
        permission_scopes=(),
        tool_allowlist=(),
    )


def _binding(run_id: str, workers: tuple[WorkerAssignment, ...]):
    now = datetime.now(UTC)
    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id=AUTHORITY_ID,
        authority_version="1",
        run_id=run_id,
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=workers,
        edges=tuple(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id=str(w.worker_id),
                relation="delegates",
            )
            for w in workers
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=1.0,
            max_total_tokens=100_000,
            max_tool_invocations=0,
            max_parallel_tasks=len(workers),
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=10),
        compiled_at=now,
    )
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={"tenant_id": ORG_ID, "idempotency_key": f"key-{run_id}"}
        ),
    )


class _Harness:
    def __init__(self, service: DirectExecutionService, execution_id: str, run_id: str):
        self.service = service
        self.execution_id = execution_id
        self.run_id = run_id

    @property
    def status(self):
        return self.service.active_executions[self.execution_id]


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: Any,
    workers: tuple[WorkerAssignment, ...],
    generate: Callable[[str, str], ModelResponse],
    *,
    run_id: str,
    wait: bool = True,
) -> _Harness:
    """``generate(persona_key, prompt)`` answers each worker's one provider call."""

    async def route_and_generate(self, request, routing_decision=None):
        persona = next(k for k, v in PERSONA.items() if v in request.prompt)
        return generate(persona, request.prompt)

    monkeypatch.setattr(ModelRouter, "route_and_generate", route_and_generate)
    if session_factory is None:
        # The mediated tool path needs a durable audit store; memory mode has none.
        monkeypatch.setattr(bridge_module.settings, "MCP_TOOL_PATH_ENABLED", False)
    router = _async_router(workers)
    service = DirectExecutionService(
        masr_router=router,
        gemini_service=object(),
        session_factory=session_factory,
        execution_authority_resolver=MappingExecutionAuthorityResolver(
            {(AUTHORITY_ID, "1"): _binding(run_id, workers)}
        ),
    )
    service.durable_write_max_attempts = 2
    service.durable_write_retry_seconds = 0
    service.durable_reconcile_interval_seconds = 60.0
    project = ResearchProject(
        title="Outcomes",
        query=ResearchQuery(
            text="What do the workers return?",
            domains=["research"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="user-1",
        scope=ResearchScope(max_sources=5),
    )
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id=AUTHORITY_ID, authority_version="1"
        ),
        organization_id=ORG_ID,
    )
    if wait:
        await asyncio.wait_for(
            asyncio.gather(*service._background_tasks, return_exceptions=True), 30
        )
    return _Harness(service, execution_id, run_id)


def _async_router(workers: tuple[WorkerAssignment, ...]) -> AsyncMock:
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub(
        agent_allocation=_AllocStub(
            worker_count=len(workers), worker_types=[w.worker_type for w in workers]
        )
    )
    return router


async def _records(session_factory: Any, run_id: str) -> dict[str, Any]:
    async with session_factory() as session:
        repo = RunLifecycleRepository(session)
        run = await repo.get_run(run_id, organization_id=ORG_ID)
        (task,) = await repo.get_tasks_for_run(run_id, organization_id=ORG_ID)
        (attempt,) = await repo.get_attempts_for_task(
            task.task_id, organization_id=ORG_ID
        )
        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, organization_id=ORG_ID
        )
        artifacts = await ArtifactRepository(session).list_artifacts_for_run(
            run_id, organization_id=ORG_ID
        )
    return {
        "run": run,
        "task": task,
        "attempt": attempt,
        "events": events,
        "artifacts": artifacts,
    }


def _assert_failed_everywhere(records: dict[str, Any], harness: _Harness) -> Any:
    assert records["run"].status == "failed"
    assert records["task"].status == "failed"
    assert records["attempt"].status == "failed"
    types = [e.event_type for e in records["events"]]
    assert types == ["run.admitted", "run.started", "run.failed"]
    assert "run.succeeded" not in types
    assert records["artifacts"] == []
    assert harness.status.status == "failed"
    assert harness.status.final_output is None
    assert harness.service.execution_stats["successful_executions"] == 0
    assert records["attempt"].journaled_result["final_output"] is None
    return records["events"][-1].payload


@pytest.fixture
def session_factory(test_engine: AsyncEngine):
    return sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_a_worker_whose_provider_call_fails_fails_every_record(
    monkeypatch, session_factory
):
    h = await _run(
        monkeypatch,
        session_factory,
        (_worker("drafting"),),
        lambda persona, prompt: _fail(prompt),
        run_id="outcome-terminal-failure",
    )

    payload = _assert_failed_everywhere(
        await _records(session_factory, h.run_id), h
    )
    assert h.status.failure_code == "WORKER_FAILED"
    assert payload["failure_code"] == "WORKER_FAILED"
    assert [(f["worker_id"], f["status"]) for f in payload["failures"]] == [
        ("w-drafting", "failed")
    ]
    await h.service.close()


@pytest.mark.asyncio
async def test_schema_invalid_output_is_invalid_output_not_success(
    monkeypatch, session_factory
):
    h = await _run(
        monkeypatch,
        session_factory,
        (_worker("drafting", _NEEDS_VERDICT),),
        lambda persona, prompt: _ok(prompt),
        run_id="outcome-invalid-output",
    )

    payload = _assert_failed_everywhere(
        await _records(session_factory, h.run_id), h
    )
    (failure,) = payload["failures"]
    assert failure["status"] == "invalid_output"
    assert failure["code"] == "OUTPUT_SCHEMA_VIOLATION"
    # Where the output is wrong, never what it said.
    assert "a usable result" not in json.dumps(payload)
    await h.service.close()


@pytest.mark.asyncio
async def test_two_successes_and_one_failure_keep_diagnostics_but_fail_the_run(
    monkeypatch, session_factory
):
    def generate(persona: str, prompt: str) -> ModelResponse:
        return _fail(prompt) if persona == "editing" else _ok(prompt)

    h = await _run(
        monkeypatch,
        session_factory,
        (_worker("content_planning"), _worker("drafting"), _worker("editing")),
        generate,
        run_id="outcome-partial",
    )

    records = await _records(session_factory, h.run_id)
    payload = _assert_failed_everywhere(records, h)
    assert [f["worker_id"] for f in payload["failures"]] == ["w-editing"]
    # The successful outputs survive as diagnostics, listed as the partial set.
    assert payload["partial_worker_ids"] == ["w-content_planning", "w-drafting"]
    journaled = records["attempt"].journaled_result["agent_results"]
    assert {"w-content_planning", "w-drafting"} <= set(journaled)
    assert journaled["w-drafting"]["content"] == "a usable result"
    await h.service.close()


@pytest.mark.asyncio
async def test_an_empty_domain_output_at_zero_confidence_is_not_success(
    monkeypatch, session_factory
):
    # No comparison items: the worker reports "success" at confidence 0.
    h = await _run(
        monkeypatch,
        session_factory,
        (_worker("comparative_analysis"),),
        lambda persona, prompt: _ok(prompt),
        run_id="outcome-zero-confidence",
    )

    payload = _assert_failed_everywhere(
        await _records(session_factory, h.run_id), h
    )
    assert [f["code"] for f in payload["failures"]] == ["ZERO_CONFIDENCE"]
    await h.service.close()


class _FlakyCommitSession(AsyncSession):
    """Refuse every commit while the shared ``armed`` flag is set."""

    armed = False

    async def commit(self) -> None:
        if type(self).armed:
            raise ConnectionError("database unavailable")
        await super().commit()


@pytest.mark.asyncio
async def test_a_failed_terminal_write_leaves_the_run_unreconciled_not_succeeded(
    monkeypatch, test_engine
):
    class Session(_FlakyCommitSession):
        armed = False

    factory = sessionmaker(test_engine, class_=Session, expire_on_commit=False)

    def generate(persona: str, prompt: str) -> ModelResponse:
        # The model call is the last thing before the terminal write.
        Session.armed = True
        return _ok(prompt)

    h = await _run(
        monkeypatch,
        factory,
        (_worker("drafting"),),
        generate,
        run_id="outcome-commit-failure",
    )
    try:
        Session.armed = False
        records = await _records(factory, h.run_id)
        assert records["run"].status == "running"
        assert records["task"].status == "running"
        assert records["attempt"].status == "running"
        assert [e.event_type for e in records["events"]] == [
            "run.admitted",
            "run.started",
        ]
        assert records["artifacts"] == []
        assert h.status.status != "completed"
        assert h.status.unconfirmed_terminal_status == "completed"
        assert h.service.execution_stats["successful_executions"] == 0

        # Existing reconciliation resolves it once the database is back.
        assert await h.service.reconcile_pending_terminal_transitions() == 1
        records = await _records(factory, h.run_id)
        assert records["run"].status == "succeeded"
        assert h.status.status == "completed"
    finally:
        await h.service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [True, False], ids=["postgres", "memory"])
async def test_memory_and_postgres_modes_agree_on_semantic_outcome(
    monkeypatch, session_factory, durable
):
    cases = {
        "success": (lambda p, t: _ok(t), (_worker("drafting"),)),
        "worker_failed": (lambda p, t: _fail(t), (_worker("drafting"),)),
        "invalid_output": (
            lambda p, t: _ok(t),
            (_worker("drafting", _NEEDS_VERDICT),),
        ),
        "zero_confidence": (lambda p, t: _ok(t), (_worker("comparative_analysis"),)),
    }
    observed: dict[str, tuple[str, str | None, bool]] = {}
    for name, (generate, workers) in cases.items():
        h = await _run(
            monkeypatch,
            session_factory if durable else None,
            workers,
            generate,
            run_id=f"outcome-parity-{name}-{'pg' if durable else 'mem'}",
        )
        observed[name] = (
            h.status.status,
            h.status.failure_code,
            h.status.final_output is not None,
        )
        await h.service.close()

    assert observed == {
        "success": ("completed", None, True),
        "worker_failed": ("failed", "WORKER_FAILED", False),
        "invalid_output": ("failed", "WORKER_FAILED", False),
        "zero_confidence": ("failed", "WORKER_FAILED", False),
    }
