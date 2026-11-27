"""Deadlines and cancellation reach real workers, not stubs.

Same path as the worker-outcome tests: the real ``DirectExecutionService`` ->
``MASRSupervisorBridge`` -> content workers against PostgreSQL, with only
``ModelRouter.route_and_generate`` replaced. The fake provider blocks, so a
deadline or a cancellation has an in-flight generation call to act on.
"""

import asyncio
import time
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.ai_brain.providers import ModelRouter
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.models.execution_authority import ExecutionAuthorityReference
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)
from tests.integration.test_direct_execution_worker_outcomes import (
    AUTHORITY_ID,
    ORG_ID,
    PERSONA,
    _async_router,
    _binding,
    _ok,
    _records,
    _worker,
)

pytestmark = [pytest.mark.integration]


class _Provider:
    """A fake provider whose ``hang`` persona blocks until cancelled."""

    def __init__(self, hang: str | None) -> None:
        self.hang = hang
        self.calls: list[str] = []
        self.cancelled: list[str] = []
        self.entered = asyncio.Event()

    async def generate(self, request: Any):
        persona = next(k for k, v in PERSONA.items() if v in request.prompt)
        self.calls.append(persona)
        if persona != self.hang:
            return _ok(request.prompt)
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.append(persona)
            raise


async def _start(
    monkeypatch: pytest.MonkeyPatch,
    test_engine: AsyncEngine,
    provider: _Provider,
    *,
    run_id: str,
    **binding_kwargs: Any,
) -> tuple[DirectExecutionService, str, Any]:

    async def route_and_generate(_router, request, routing_decision=None):
        return await provider.generate(request)

    monkeypatch.setattr(ModelRouter, "route_and_generate", route_and_generate)
    factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    workers = (_worker("content_planning"), _worker("drafting"))
    service = DirectExecutionService(
        masr_router=_async_router(workers),
        gemini_service=object(),
        session_factory=factory,
        execution_authority_resolver=MappingExecutionAuthorityResolver(
            {
                (AUTHORITY_ID, "1"): _binding(
                    run_id,
                    workers,
                    dependencies=(("w-content_planning", "w-drafting"),),
                    **binding_kwargs,
                )
            }
        ),
    )
    service.durable_write_max_attempts = 2
    service.durable_write_retry_seconds = 0
    service.durable_reconcile_interval_seconds = 60.0
    project = ResearchProject(
        title="Lifetime",
        query=ResearchQuery(
            text="What takes too long?",
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
    return service, execution_id, factory


async def _finish(service: DirectExecutionService) -> None:
    await asyncio.wait_for(
        asyncio.gather(*service._background_tasks, return_exceptions=True), 30
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"task_timeout_seconds": 1}, "TASK_TIMEOUT"),
        ({"deadline_seconds": 1.5}, "DEADLINE_EXCEEDED"),
    ],
    ids=["task-timeout", "plan-deadline"],
)
async def test_a_worker_past_its_limit_times_out_and_fails_the_run(
    monkeypatch, test_engine, kwargs, code
):
    provider = _Provider(hang="content_planning")
    run_id = f"lifetime-{code.lower()}"
    started = time.monotonic()
    service, execution_id, factory = await _start(
        monkeypatch, test_engine, provider, run_id=run_id, **kwargs
    )
    await _finish(service)

    assert time.monotonic() - started < 10
    # The in-flight generation was stopped, and the dependent never dispatched.
    assert provider.cancelled == ["content_planning"]
    assert provider.calls == ["content_planning"]
    status = service.active_executions[execution_id]
    assert status.status == "failed"
    assert status.failure_code == code
    records = await _records(factory, run_id)
    assert records["run"].status == "failed"
    assert records["task"].status == "failed"
    assert records["attempt"].status == "timed_out"
    assert [e.event_type for e in records["events"]] == [
        "run.admitted",
        "run.started",
        "run.failed",
    ]
    payload = records["events"][-1].payload
    assert payload["failure_code"] == code
    assert {(f["worker_id"], f["status"], f["code"]) for f in payload["failures"]} == {
        ("w-content_planning", "timed_out", code),
        ("w-drafting", "blocked", "UPSTREAM_FAILED"),
    }
    await service.close()


@pytest.mark.asyncio
async def test_cancelling_mid_generation_stops_the_call_and_the_dependents(
    monkeypatch, test_engine
):
    provider = _Provider(hang="content_planning")
    run_id = "lifetime-cancel"
    service, execution_id, factory = await _start(
        monkeypatch, test_engine, provider, run_id=run_id
    )
    await asyncio.wait_for(provider.entered.wait(), 10)

    assert await service.cancel_execution(execution_id) is True
    await _finish(service)

    assert provider.cancelled == ["content_planning"]
    assert provider.calls == ["content_planning"]
    status = service.active_executions[execution_id]
    assert status.status == "cancelled"
    records = await _records(factory, run_id)
    assert records["run"].status == "cancelled"
    assert records["task"].status == "cancelled"
    assert records["attempt"].status == "cancelled"
    assert [e.event_type for e in records["events"]] == [
        "run.admitted",
        "run.started",
        "run.cancellation_requested",
        "run.cancelled",
    ]
    assert records["artifacts"] == []
    await service.close()
