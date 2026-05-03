"""Characterization tests for supervisor registry extraction."""

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.api.services.supervisor_registry import SupervisorRegistry
from src.models.supervisor_api_models import SupervisorType, WorkerStatus


def test_registry_initializes_all_supervisor_types() -> None:
    registry = SupervisorRegistry()

    assert set(registry.supervisors) == {supervisor.value for supervisor in SupervisorType}
    assert set(registry.workers) == set(registry.supervisors)
    assert set(registry.metrics) == set(registry.supervisors)


def test_registry_creates_research_workers_with_existing_shape() -> None:
    registry = SupervisorRegistry()

    workers = registry.workers[SupervisorType.RESEARCH.value]

    assert [worker.worker_type for worker in workers] == [
        "literature_review",
        "comparative_analysis",
        "methodology",
        "synthesis",
        "citation",
    ]
    assert all(worker.status == WorkerStatus.IDLE for worker in workers)
    assert all(worker.worker_id.startswith("research-worker-") for worker in workers)


def test_service_keeps_registry_state_aliases() -> None:
    service = SupervisorCoordinationService()

    assert service.supervisors is service.registry.supervisors
    assert service.workers is service.registry.workers
    assert service.metrics is service.registry.metrics
    assert service._get_worker_capabilities("writer") == [
        "create",
        "draft",
        "structure",
        "style",
    ]
