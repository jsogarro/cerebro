"""Resilience characterization tests for bounded workflow iteration."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.ai_brain.router.masr import MASRouter
from src.api.services.talkhier_round_executor import TalkHierRoundExecutor
from src.api.services.talkhier_session_service import TalkHierSession
from src.api.services.talkhier_state_manager import TalkHierStateManager
from src.models.talkhier_api_models import (
    ConsensusType,
    ProtocolType,
    RefinementRoundRequest,
    RefinementStrategy,
    SessionStatus,
)
from src.orchestration.graph_builder import GraphConfig, ResearchGraphBuilder
from src.orchestration.state import (
    MaxIterationsExceeded,
    ResearchState,
    WorkflowPhase,
)
from src.reliability.connection_pools import PoolStatus, RedisPoolManager
from src.reliability.retry_strategies import CircuitBreaker
from src.services.gemini_service import GeminiService


def build_state() -> ResearchState:
    return ResearchState(
        project_id="project-1",
        workflow_id="workflow-1",
        query="bounded workflow",
        domains=["general"],
    )


def test_research_state_tracks_iteration_count_and_raises_at_limit() -> None:
    state = build_state()

    state.increment_iteration(max_iterations=2, node_name="initialization")
    state.increment_iteration(max_iterations=2, node_name="query_analysis")

    assert state.iteration_count == 2
    with pytest.raises(MaxIterationsExceeded, match="exceeded max_iterations=2"):
        state.increment_iteration(max_iterations=2, node_name="plan_generation")


def test_graph_builder_enforces_max_iterations_before_node_execution() -> None:
    executed: list[str] = []

    def handler(state: ResearchState) -> ResearchState:
        executed.append("handler")
        return state

    builder = ResearchGraphBuilder(GraphConfig(max_iterations=1))
    wrapped = builder._wrap_handler(
        builder.add_node(
            "initialization",
            handler,
            WorkflowPhase.INITIALIZATION,
        ).nodes["initialization"]
    )

    state = build_state()
    assert wrapped(state) is state
    assert state.iteration_count == 1
    assert executed == ["handler"]

    with pytest.raises(MaxIterationsExceeded):
        wrapped(state)

    assert executed == ["handler"]


def test_talkhier_refinement_round_rejects_rounds_past_max_rounds() -> None:
    async def execute_past_cap() -> None:
        await TalkHierRoundExecutor().execute_refinement_round(
            "session-1",
            session,
            RefinementRoundRequest(round_number=2),
            TalkHierStateManager(),
        )

    session = TalkHierSession(
        session_id="session-1",
        query="bounded TalkHier",
        domains=["general"],
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(UTC),
        protocol_type=ProtocolType.STANDARD,
        refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
        max_rounds=1,
        min_rounds=1,
        quality_threshold=0.9,
        consensus_type=ConsensusType.WEIGHTED,
        consensus_threshold=0.9,
        timeout_seconds=300,
        participants=[],
        current_round=1,
    )

    with pytest.raises(ValueError, match="exceeds max_rounds=1"):
        asyncio.run(execute_past_cap())


def test_gemini_api_call_uses_tenacity_retry_policy() -> None:
    assert hasattr(GeminiService._generate_content, "retry")


def test_masr_router_exposes_routing_circuit_breaker() -> None:
    router = MASRouter(config={"enable_learning": False})

    assert isinstance(router.routing_circuit_breaker, CircuitBreaker)


def test_redis_pool_health_check_marks_pool_unhealthy_on_ping_failure() -> None:
    async def run_check() -> PoolStatus:
        manager = RedisPoolManager()
        manager._client = AsyncMock()
        manager._client.ping.side_effect = ConnectionError("redis down")
        return await manager.health_check()

    assert asyncio.run(run_check()) == PoolStatus.UNHEALTHY
