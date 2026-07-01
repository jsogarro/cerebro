"""Regression tests for real supervisor-coordination execution.

The coordination service previously returned stubs: `coordinate_workers` returned
a plan with no results, `_execute_with_workers` returned a formatted string, and
`resolve_conflict` returned placeholder text ("Supervisor decision", "Weighted
consensus of all outputs", "Resolution through structured debate") for several
strategies. These tests assert the service now performs real execution/adjudication.
"""

import pytest

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.models.supervisor_api_models import (
    ConflictResolutionRequest,
    ConflictResolutionStrategy,
    CoordinationMode,
    WorkerCoordinationRequest,
)


class _FakeGemini:
    async def generate_content(self, prompt: str) -> str:
        return "RESOLUTION: synthesized answer\nCONFIDENCE: 0.9\nREASONING: merged both"


class _FakeExecutor:
    def __init__(self) -> None:
        self.called = False

    async def execute_with_workers(self, *args, **kwargs):
        self.called = True
        return {"summary": "real coordinated result", "_execution_metadata": {}}


@pytest.fixture
def service() -> SupervisorCoordinationService:
    return SupervisorCoordinationService()


async def test_resolve_conflict_uses_real_adjudication_not_placeholder(
    service: SupervisorCoordinationService,
) -> None:
    service._gemini_service = _FakeGemini()
    request = ConflictResolutionRequest(
        conflict_id="c1",
        worker_outputs=[
            {"worker_id": "w1", "output": "answer A", "confidence": 0.6},
            {"worker_id": "w2", "output": "answer B", "confidence": 0.7},
        ],
        resolution_strategy=ConflictResolutionStrategy.WEIGHTED_CONSENSUS,
    )

    resp = await service.resolve_conflict(request)

    assert resp.resolved_output == "synthesized answer"
    assert resp.resolved_output != "Weighted consensus of all outputs"
    assert resp.confidence_score == pytest.approx(0.9)


async def test_supervisor_override_with_guidance_is_respected(
    service: SupervisorCoordinationService,
) -> None:
    request = ConflictResolutionRequest(
        conflict_id="c2",
        worker_outputs=[{"worker_id": "w1", "output": "x", "confidence": 0.5}],
        resolution_strategy=ConflictResolutionStrategy.SUPERVISOR_OVERRIDE,
        supervisor_guidance="use approach X",
    )

    resp = await service.resolve_conflict(request)

    assert resp.resolved_output == "use approach X"
    assert resp.resolved_output != "Supervisor decision"


async def test_coordinate_workers_executes_and_returns_results(
    service: SupervisorCoordinationService,
) -> None:
    fake = _FakeExecutor()
    service._real_executor = fake

    request = WorkerCoordinationRequest(
        task="compare two proposals",
        worker_types=["literature_review", "comparative_analysis"],
        coordination_mode=CoordinationMode.PARALLEL,
        parameters={},
    )

    resp = await service.coordinate_workers("research", request)

    assert fake.called, "coordinate_workers did not execute the workers"
    assert resp.status == "completed"
    assert resp.coordination_plan["execution_result"] == {
        "summary": "real coordinated result",
        "_execution_metadata": {},
    }


async def test_execute_with_workers_uses_real_executor(
    service: SupervisorCoordinationService,
) -> None:
    fake = _FakeExecutor()
    service._real_executor = fake

    from src.models.supervisor_api_models import CoordinationMode, SupervisionStrategy

    result = await service._execute_with_workers(
        "research",
        "task",
        [],
        SupervisionStrategy.COLLABORATIVE,
        CoordinationMode.PARALLEL,
        0.8,
        120,
    )

    assert fake.called
    assert result["summary"] == "real coordinated result"
