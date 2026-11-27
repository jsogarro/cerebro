"""Tool-call identity is scoped to the admitted worker, not only the root task.

Every worker in a plan runs under the run's one durable root task and attempt,
so run/task/attempt alone cannot tell two workers' identical calls apart: the
second would replay the first worker's result instead of making its own call.
The scope comes from the plan the service admitted (the task context), never
from tool arguments or client input.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.models import AgentTask
from src.agents.tools.mediation import (
    WORKER_SCOPE_CONTEXT_KEY,
    InMemoryToolAuditStore,
    ToolCallIdentity,
)
from src.agents.tools.registry import create_default_registry

PARAMS = {"expression": "2 + 3"}


def _task(scope: str | None, **extra: Any) -> AgentTask:
    context: dict[str, Any] = {
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        **extra,
    }
    if scope is not None:
        context[WORKER_SCOPE_CONTEXT_KEY] = scope
    return AgentTask(
        id=f"task-1:{scope}", agent_type="x", input_data={}, context=context
    )


def _keys(store: InMemoryToolAuditStore) -> set[str]:
    return {invocation.idempotency_key for invocation in store.invocations}


@pytest.mark.asyncio
async def test_two_workers_making_the_same_call_get_independent_invocations() -> None:
    store = InMemoryToolAuditStore()
    registry = create_default_registry(audit_store=store)

    for scope in ("plan-1:worker-a", "plan-1:worker-b"):
        result = await registry.execute(
            "arithmetic",
            PARAMS,
            identity=ToolCallIdentity.from_agent_task(_task(scope)),
        )
        assert result.success is True

    assert len(_keys(store)) == 2


@pytest.mark.asyncio
async def test_a_retry_by_the_same_worker_reuses_its_invocation() -> None:
    store = InMemoryToolAuditStore()
    registry = create_default_registry(audit_store=store)
    identity = ToolCallIdentity.from_agent_task(_task("plan-1:worker-a"))

    await registry.execute("arithmetic", PARAMS, identity=identity)
    await registry.execute("arithmetic", PARAMS, identity=identity)

    assert len(_keys(store)) == 1


@pytest.mark.asyncio
async def test_a_stale_plan_does_not_replay_a_newer_plans_call() -> None:
    store = InMemoryToolAuditStore()
    registry = create_default_registry(audit_store=store)

    for scope in ("plan-old:worker-a", "plan-new:worker-a"):
        await registry.execute(
            "arithmetic",
            PARAMS,
            identity=ToolCallIdentity.from_agent_task(_task(scope)),
        )

    assert len(_keys(store)) == 2


@pytest.mark.asyncio
async def test_a_worker_id_in_the_arguments_cannot_change_the_scope() -> None:
    store = InMemoryToolAuditStore()
    registry = create_default_registry(audit_store=store)
    identity = ToolCallIdentity.from_agent_task(_task("plan-1:worker-a"))

    await registry.execute("arithmetic", PARAMS, identity=identity)
    await registry.execute(
        "arithmetic",
        {**PARAMS, "worker_scope": "plan-1:worker-b", "worker_id": "worker-b"},
        identity=identity,
    )

    # The spoofed fields are not tool parameters; the scope is still worker-a's.
    assert {i.run_id for i in store.invocations} == {"run-1"}
    assert len(_keys(store)) == 1


def test_an_unscoped_task_has_no_worker_scope() -> None:
    identity = ToolCallIdentity.from_agent_task(_task(None))

    assert identity.worker_scope is None
