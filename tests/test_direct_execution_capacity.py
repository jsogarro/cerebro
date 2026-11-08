"""Admission cap counts in-flight executions, not finished ones (issue #143)."""

import asyncio
import dataclasses
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.api.services.direct_execution_service import DirectExecutionService
from src.models.execution_authority import ExecutionAuthorityReference
from tests.test_direct_execution_authority import (
    OWNING_ORGANIZATION,
    _binding,
    _Decision,
    _project,
    _service,
)


def _capacity_service(
    cap: int, gate: asyncio.Event | None = None
) -> DirectExecutionService:
    """Service whose resolver mints a fresh run per call; workflow is mocked."""
    base = _binding()
    counter = iter(range(10_000))
    resolver = Mock()
    resolver.resolve.side_effect = lambda reference, organization_id=None: (
        dataclasses.replace(
            base, run=base.run.model_copy(update={"run_id": f"run-{next(counter)}"})
        )
    )
    service, router = _service(resolver=resolver)
    # The authority module's decision shares a mutable class-level allocation
    # that another test rewrites; pin a private one so order cannot matter.
    decision = _Decision()
    decision.agent_allocation = SimpleNamespace(
        supervisor_type="research", worker_types=["literature", "synthesis"]
    )
    router.route.return_value = decision
    service.max_concurrent_executions = cap

    async def fake_workflow(project, execution_status, *args, **kwargs) -> None:
        execution_status.status = "running"
        if gate is not None:
            await gate.wait()
        execution_status.status = "completed"

    service._execute_research_workflow = fake_workflow  # type: ignore[method-assign]
    return service


async def _admit(service: DirectExecutionService) -> str:
    return await service.start_research_execution(
        _project(),
        authority_reference=ExecutionAuthorityReference(
            authority_id="authority-1", authority_version="1"
        ),
        organization_id=OWNING_ORGANIZATION,
    )


async def _drain(service: DirectExecutionService) -> None:
    await asyncio.gather(*list(service._background_tasks))


@pytest.mark.asyncio
async def test_finished_executions_do_not_consume_capacity() -> None:
    service = _capacity_service(cap=50)
    ids = []
    for _ in range(50):
        ids.append(await _admit(service))
        await _drain(service)

    ids.append(await _admit(service))
    ids.append(await _admit(service))
    await _drain(service)

    assert len(set(ids)) == 52
    # Finished runs stay readable.
    assert service.active_executions[ids[0]].status == "completed"
    await service.close()


@pytest.mark.asyncio
async def test_cap_still_refuses_when_genuinely_running() -> None:
    gate = asyncio.Event()
    service = _capacity_service(cap=50, gate=gate)
    for _ in range(50):
        await _admit(service)
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="Maximum concurrent executions"):
        await _admit(service)

    gate.set()
    await _drain(service)
    await _admit(service)  # capacity returns once they finish
    await _drain(service)
    await service.close()
