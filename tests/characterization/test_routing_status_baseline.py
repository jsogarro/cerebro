"""Pin lossy routing translations and parallel legacy status vocabularies."""

import pytest

from src.ai_brain.integration.masr_supervisor_bridge import RoutingDecisionTranslator
from src.ai_brain.router.cost_optimizer import ModelSpec, ModelTier, OptimizationResult
from src.ai_brain.router.masr import RoutingDecision
from src.ai_brain.router.query_analyzer import (
    ComplexityAnalysis,
    ComplexityFactors,
    ComplexityLevel,
    QueryDomain,
)
from src.ai_brain.router.routing_types import (
    AgentAllocation,
    CollaborationMode,
    RoutingStrategy,
)
from src.api.services.real_supervisor_executor import RealSupervisorExecutor
from src.models.db.agent_task import TaskStatus
from src.models.research_project import ResearchStatus
from src.models.supervisor_api_models import CoordinationMode
from src.models.websocket_messages import WSMessageType
from tests.fixtures.agent_system.baseline import failure_taxonomy_by_name


def _routing_decision(mode: CollaborationMode) -> RoutingDecision:
    model = ModelSpec("fixture", "fixture", ModelTier.BASIC, 0.0, 1, 1)
    return RoutingDecision(
        query_id="query-1",
        timestamp=__import__("datetime").datetime.now(),
        complexity_analysis=ComplexityAnalysis(
            score=0.2,
            level=ComplexityLevel.SIMPLE,
            factors=ComplexityFactors(),
            domains=[QueryDomain.RESEARCH],
        ),
        optimization_result=OptimizationResult(primary_model=model),
        collaboration_mode=mode,
        agent_allocation=AgentAllocation(
            supervisor_type="general", worker_count=2, worker_types=["worker"]
        ),
        estimated_cost=0.01,
        estimated_latency_ms=10,
        estimated_quality=0.9,
        confidence_score=0.8,
        routing_strategy=RoutingStrategy.QUALITY_FOCUSED,
    )


@pytest.mark.parametrize(
    ("mode", "execution_mode"),
    [
        (CollaborationMode.FAST_PATH, "parallel"),
        (CollaborationMode.DIRECT, "sequential"),
        (CollaborationMode.PARALLEL, "parallel"),
        (CollaborationMode.HIERARCHICAL, "hybrid"),
        (CollaborationMode.DEBATE, "adaptive"),
        (CollaborationMode.ENSEMBLE, "parallel"),
    ],
)
def test_all_advertised_collaboration_modes_have_observed_bridge_translation(
    mode, execution_mode
):
    config = RoutingDecisionTranslator().translate(_routing_decision(mode))

    assert config.execution_mode == execution_mode
    assert config.context["collaboration_mode"] == mode.value
    assert config.supervisor_type == "research"


def test_bridge_ignores_explicit_routing_strategy_when_deriving_quality_policy():
    config = RoutingDecisionTranslator().translate(
        _routing_decision(CollaborationMode.HIERARCHICAL)
    )

    assert config.routing_strategy == "balanced"
    assert config.quality_threshold == 0.85
    assert config.max_refinement_rounds == 1


@pytest.mark.parametrize(
    ("mode", "execution_mode"),
    [
        (CoordinationMode.SEQUENTIAL, "sequential"),
        (CoordinationMode.PARALLEL, "parallel"),
        (CoordinationMode.HIERARCHICAL, "hybrid"),
        (CoordinationMode.ADAPTIVE, "adaptive"),
        (CoordinationMode.DEBATE, "parallel"),
    ],
)
def test_supervisor_coordination_debate_silently_defaults_to_parallel(
    mode, execution_mode
):
    executor = object.__new__(RealSupervisorExecutor)

    assert executor._coordination_mode_to_execution_mode(mode) == execution_mode


def test_parallel_status_and_event_vocabularies_remain_legacy_compatibility_inputs():
    assert {status.value for status in ResearchStatus} == {
        "pending",
        "planning",
        "in_progress",
        "completed",
        "failed",
        "cancelled",
    }
    assert {status.value for status in TaskStatus} == {
        "pending",
        "queued",
        "in_progress",
        "completed",
        "failed",
        "cancelled",
        "retrying",
    }
    assert {event.value for event in WSMessageType} >= {
        "project.started",
        "project.completed",
        "project.failed",
        "project.cancelled",
        "agent.started",
        "agent.completed",
        "agent.failed",
    }


def test_failure_taxonomy_fixture_makes_unimplemented_failure_controls_explicit():
    taxonomy = failure_taxonomy_by_name()

    assert set(taxonomy) == {
        "specification",
        "coordination",
        "verification_termination",
        "tool",
        "provider",
        "persistence",
        "human_control",
    }
    assert "lossy" in taxonomy["coordination"]
    assert "No canonical" in taxonomy["specification"]
