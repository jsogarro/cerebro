"""Supervisor API responses must be computed from real data or explicitly absent."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.api.services.supervisor_registry import SupervisorRegistry
from src.models.supervisor_api_models import (
    ConflictResolutionRequest,
    ConflictResolutionStrategy,
    ExperimentRequest,
    SupervisionStrategy,
    SupervisorType,
    WorkerAllocationOptimizationRequest,
)

ROOT = Path(__file__).resolve().parents[1]
RESPONSE_PATH_MODULES = [
    *sorted((ROOT / "src/api/services").glob("supervisor_*.py")),
    ROOT / "src/api/routes/supervisor_api.py",
    ROOT / "src/models/supervisor_api_models.py",
]


@pytest.mark.parametrize("path", RESPONSE_PATH_MODULES, ids=lambda p: p.name)
def test_response_path_modules_do_not_import_random(path: Path) -> None:
    imported = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "random" not in imported


def test_guard_covers_the_supervisor_modules() -> None:
    names = {p.name for p in RESPONSE_PATH_MODULES}
    assert {"supervisor_coordination_service.py", "supervisor_registry.py"} <= names


def test_worker_performance_score_is_not_measured() -> None:
    workers = SupervisorRegistry().create_workers_for_supervisor(
        SupervisorType.RESEARCH
    )
    assert workers
    assert all(w.performance_score is None for w in workers)


@pytest.mark.asyncio
async def test_conflict_resolution_reports_consensus_as_not_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.api.services.real_supervisor_executor.resolve_conflict_with_supervisor",
        AsyncMock(return_value=("a", 0.7, "because")),
    )
    service = SupervisorCoordinationService()
    service._get_gemini_service = MagicMock(return_value=None)  # type: ignore[method-assign]
    request = ConflictResolutionRequest(
        conflict_id="c1",
        worker_outputs=[
            {"worker_id": "w1", "output": "a"},
            {"worker_id": "w2", "output": "b"},
        ],
        resolution_strategy=ConflictResolutionStrategy.MAJORITY_VOTE,
    )

    response = await service.resolve_conflict(request)

    assert response.worker_consensus is None
    assert response.confidence_score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_allocation_optimization_has_no_invented_alternatives() -> None:
    service = SupervisorCoordinationService()
    request = WorkerAllocationOptimizationRequest(
        task_requirements={"complexity": 0.5, "quality_target": 0.8},
        available_workers=8,
        optimization_goal="balanced",
    )

    response = await service.optimize_worker_allocation(request)

    assert response.alternative_allocations is None


@pytest.mark.asyncio
async def test_experiment_is_not_implemented_rather_than_simulated() -> None:
    service = SupervisorCoordinationService()
    request = ExperimentRequest(
        query="q",
        strategies_to_test=[SupervisionStrategy.DIRECT],
        metrics_to_track=["quality"],
    )

    with pytest.raises(HTTPException) as exc:
        await service.run_experiment(request)

    assert exc.value.status_code == 501


def test_experiment_route_returns_501_not_500() -> None:
    from collections.abc import AsyncIterator
    from unittest.mock import Mock

    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.middleware.auth_middleware import get_jwt_service
    from src.models.db.session import get_session
    from tests.test_supervisor_execution_no_fabrication import _JWT

    async def _session() -> AsyncIterator[Mock]:
        yield Mock(sync_session=Session())

    missing = object()
    previous = getattr(app.state, "supervisor_coordination_service", missing)
    app.state.supervisor_coordination_service = SupervisorCoordinationService()
    app.dependency_overrides[get_jwt_service] = lambda: _JWT()
    app.dependency_overrides[get_session] = _session
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/supervisors/experiment",
            json={
                "query": "q",
                "strategies_to_test": ["direct"],
                "metrics_to_track": ["quality"],
            },
            headers={"Authorization": "Bearer t"},
        )
    finally:
        client.close()
        app.dependency_overrides.pop(get_jwt_service, None)
        app.dependency_overrides.pop(get_session, None)
        if previous is missing:
            del app.state.supervisor_coordination_service
        else:
            app.state.supervisor_coordination_service = previous

    assert response.status_code == 501, response.text
