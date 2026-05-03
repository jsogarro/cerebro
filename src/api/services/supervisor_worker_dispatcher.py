"""Worker assignment and execution helpers for supervisor coordination."""

import asyncio
from collections.abc import Callable
from typing import Any

from src.models.supervisor_api_models import (
    CoordinationMode,
    SupervisionStrategy,
    WorkerInfo,
    WorkerStatus,
)


class WorkerDispatcher:
    """Assigns workers and simulates worker execution for supervisors."""

    def __init__(
        self,
        workers: dict[str, list[WorkerInfo]],
        get_worker_capabilities: Callable[[str], list[str]],
    ) -> None:
        self.workers = workers
        self.get_worker_capabilities = get_worker_capabilities

    async def assign_workers_for_task(
        self,
        supervisor_type: str,
        task: str,
        max_workers: int,
        coordination_mode: CoordinationMode,
    ) -> list[WorkerInfo]:
        """Assign idle workers for a task based on coordination requirements."""
        available_workers = [
            worker
            for worker in self.workers[supervisor_type]
            if worker.status == WorkerStatus.IDLE
        ]

        if coordination_mode == CoordinationMode.SEQUENTIAL:
            num_workers = min(1, len(available_workers))
        elif coordination_mode == CoordinationMode.PARALLEL:
            num_workers = min(max_workers, len(available_workers))
        else:
            num_workers = min(max(3, max_workers // 2), len(available_workers))

        selected_workers = sorted(
            available_workers,
            key=lambda worker: worker.performance_score or 0,
            reverse=True,
        )[:num_workers]

        for worker in selected_workers:
            worker.status = WorkerStatus.ASSIGNED
            worker.current_task = task[:50]

        return selected_workers

    async def execute_with_workers(
        self,
        supervisor_type: str,
        task: str,
        workers: list[WorkerInfo],
        strategy: SupervisionStrategy,
        coordination_mode: CoordinationMode,
        quality_threshold: float,
        timeout_seconds: int,
    ) -> Any | None:
        """Execute a task with assigned workers."""
        _ = (supervisor_type, coordination_mode, quality_threshold, timeout_seconds)
        await asyncio.sleep(0.5)

        for worker in workers:
            worker.status = WorkerStatus.EXECUTING

        if strategy == SupervisionStrategy.DIRECT:
            result = f"Direct execution result for: {task}"
        elif strategy == SupervisionStrategy.COLLABORATIVE:
            result = f"Collaborative result from {len(workers)} workers for: {task}"
        elif strategy == SupervisionStrategy.ITERATIVE:
            result = f"Iterative refinement result after multiple rounds for: {task}"
        else:
            result = f"Execution result for: {task}"

        for worker in workers:
            worker.status = WorkerStatus.COMPLETED
            worker.current_task = None

        await asyncio.sleep(0.1)
        for worker in workers:
            worker.status = WorkerStatus.IDLE

        return result

    async def assign_workers_for_coordination(
        self,
        supervisor_type: str,
        worker_types: list[str],
        coordination_mode: CoordinationMode,
    ) -> list[WorkerInfo]:
        """Assign specific worker types for coordination."""
        _ = coordination_mode
        assigned: list[WorkerInfo] = []
        available_workers = self.workers.get(supervisor_type, [])

        for worker_type in worker_types:
            matching_workers = [
                worker
                for worker in available_workers
                if worker.worker_type == worker_type and worker.status == WorkerStatus.IDLE
            ]

            if matching_workers:
                worker = matching_workers[0]
                worker.status = WorkerStatus.ASSIGNED
                assigned.append(worker)
            else:
                worker = WorkerInfo(
                    worker_id=f"{supervisor_type}-{worker_type}-temp",
                    worker_type=worker_type,
                    status=WorkerStatus.ASSIGNED,
                    capabilities=self.get_worker_capabilities(worker_type),
                    performance_score=0.85,
                    current_task=None,
                )
                assigned.append(worker)

        return assigned
