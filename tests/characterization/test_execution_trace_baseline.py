"""Credential-free trace of the active DirectExecutionService pipeline."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai_brain.router.routing_types import CollaborationMode
from src.api.services.direct_execution_service import DirectExecutionService
from src.models.research_project import ResearchProject, ResearchQuery, ResearchScope


@dataclass
class _Allocation:
    supervisor_type: str = "research"
    worker_count: int = 2
    worker_types: list[str] = field(default_factory=lambda: ["literature", "synthesis"])
    max_parallel: int = 2
    timeout_seconds: int = 300


@dataclass
class _Complexity:
    decomposition: None = None


@dataclass
class _Decision:
    query_id: str = "route-1"
    collaboration_mode: CollaborationMode = CollaborationMode.HIERARCHICAL
    agent_allocation: _Allocation = field(default_factory=_Allocation)
    complexity_analysis: _Complexity = field(default_factory=_Complexity)
    estimated_cost: float = 0.01
    estimated_latency_ms: int = 10
    estimated_quality: float = 0.8
    confidence_score: float = 0.8
    context_requirements: dict = field(default_factory=dict)
    memory_allocation: dict = field(default_factory=dict)


@pytest.mark.asyncio
async def test_direct_execution_routes_to_bridge_with_legacy_agent_task_and_verification_output():
    router = AsyncMock()
    router.route.return_value = _Decision()
    bridge = AsyncMock()
    bridge_result = SimpleNamespace(
        status=SimpleNamespace(value="completed"),
        agent_result=SimpleNamespace(
            output={
                "research_findings": ["fixture"],
                "quality_metrics": {"confidence": 0.8},
            }
        ),
        quality_score=0.8,
        consensus_score=0.7,
        workers_used=2,
        execution_time_seconds=0.01,
        errors=[],
    )
    bridge.execute_routing_decision.return_value = bridge_result
    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        event_publisher=AsyncMock(),
    )
    project = ResearchProject(
        title="baseline trace",
        query=ResearchQuery(text="Characterize active execution", domains=["research"]),
        user_id="baseline-user",
        scope=ResearchScope(),
    )

    execution_id = await service.start_research_execution(project)
    await asyncio.gather(*tuple(service._background_tasks))
    observed = await service.get_execution_status(execution_id)

    router.route.assert_awaited_once()
    bridge.execute_routing_decision.assert_awaited_once()
    task = bridge.execute_routing_decision.await_args.kwargs["task"]
    assert task.agent_type == "research"
    assert task.input_data["routing_decision"]["collaboration_mode"] == "hierarchical"
    assert observed.status == "completed"
    assert observed.current_phase == "completed"
    assert observed.final_output == {
        "research_findings": ["fixture"],
        "quality_metrics": {"confidence": 0.8},
    }
    assert observed.quality_scores == {"overall": 0.8, "consensus": 0.7}
