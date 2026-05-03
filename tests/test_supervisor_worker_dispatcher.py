"""Characterization tests for supervisor worker dispatch extraction."""

import pytest

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.api.services.supervisor_registry import SupervisorRegistry
from src.api.services.supervisor_worker_dispatcher import WorkerDispatcher
from src.models.supervisor_api_models import (
    CoordinationMode,
    SupervisionStrategy,
    SupervisorType,
    WorkerStatus,
)


@pytest.mark.asyncio
async def test_dispatcher_assigns_highest_scoring_parallel_workers() -> None:
    registry = SupervisorRegistry()
    dispatcher = WorkerDispatcher(registry.workers, registry.get_worker_capabilities)

    workers = registry.workers[SupervisorType.RESEARCH.value]
    for index, worker in enumerate(workers):
        worker.performance_score = 0.8 + (index / 100)

    assigned = await dispatcher.assign_workers_for_task(
        SupervisorType.RESEARCH.value,
        "x" * 80,
        max_workers=3,
        coordination_mode=CoordinationMode.PARALLEL,
    )

    assert len(assigned) == 3
    assert assigned == sorted(
        workers,
        key=lambda worker: worker.performance_score or 0,
        reverse=True,
    )[:3]
    assert all(worker.status == WorkerStatus.ASSIGNED for worker in assigned)
    assert all(worker.current_task == "x" * 50 for worker in assigned)


@pytest.mark.asyncio
async def test_dispatcher_executes_workers_and_resets_status() -> None:
    registry = SupervisorRegistry()
    dispatcher = WorkerDispatcher(registry.workers, registry.get_worker_capabilities)
    workers = registry.workers[SupervisorType.RESEARCH.value][:2]

    result = await dispatcher.execute_with_workers(
        SupervisorType.RESEARCH.value,
        "summarize evidence",
        workers,
        SupervisionStrategy.COLLABORATIVE,
        CoordinationMode.HIERARCHICAL,
        quality_threshold=0.8,
        timeout_seconds=120,
    )

    assert result == "Collaborative result from 2 workers for: summarize evidence"
    assert all(worker.status == WorkerStatus.IDLE for worker in workers)
    assert all(worker.current_task is None for worker in workers)


@pytest.mark.asyncio
async def test_dispatcher_assigns_coordination_placeholder_worker() -> None:
    registry = SupervisorRegistry()
    dispatcher = WorkerDispatcher(registry.workers, registry.get_worker_capabilities)

    assigned = await dispatcher.assign_workers_for_coordination(
        SupervisorType.RESEARCH.value,
        ["unavailable_specialist"],
        CoordinationMode.HIERARCHICAL,
    )

    assert len(assigned) == 1
    assert assigned[0].worker_id == "research-unavailable_specialist-temp"
    assert assigned[0].status == WorkerStatus.ASSIGNED
    assert assigned[0].capabilities == ["execute", "report"]


@pytest.mark.asyncio
async def test_service_keeps_worker_dispatcher_delegates() -> None:
    service = SupervisorCoordinationService()

    assigned = await service._assign_workers_for_task(
        SupervisorType.CONTENT.value,
        "draft article",
        max_workers=1,
        coordination_mode=CoordinationMode.SEQUENTIAL,
    )

    assert service.worker_dispatcher.workers is service.workers
    assert len(assigned) == 1
    assert assigned[0].status == WorkerStatus.ASSIGNED
