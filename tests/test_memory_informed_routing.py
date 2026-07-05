"""Tests for memory-informed routing feature.

Tests the bounded memory influence on routing decisions and worker prompts,
including feature flag behavior, resilience, and cap enforcement.
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask
from src.ai_brain.memory.episodic_memory import Episode, EventType
from src.ai_brain.memory.procedural_memory import (
    Procedure,
    ProcedureType,
)
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import ComplexityLevel


@pytest.fixture
def mock_episodic_memory():
    """Mock episodic memory manager."""
    mem = Mock()
    mem.retrieve_episodes = AsyncMock(return_value=[])
    return mem


@pytest.fixture
def mock_procedural_memory():
    """Mock procedural memory manager."""
    mem = Mock()
    mem.retrieve_procedures = AsyncMock(return_value=[])
    return mem


@pytest.fixture
def masr_router_flag_off():
    """MASR router with memory-informed routing DISABLED (default)."""
    config = {
        "memory_informed_routing_enabled": False,
        "default_strategy": "balanced",
    }
    return MASRouter(config=config)


@pytest.fixture
def masr_router_flag_on():
    """MASR router with memory-informed routing ENABLED."""
    config = {
        "memory_informed_routing_enabled": True,
        "memory_routing_max_worker_adjust": 2,
        "memory_routing_freshness_days": 30,
        "default_strategy": "balanced",
    }
    return MASRouter(config=config)


# ============================================================================
# Feature Flag Tests - Default OFF Behavior
# ============================================================================


@pytest.mark.asyncio
async def test_flag_off_no_memory_calls(masr_router_flag_off, mock_episodic_memory):
    """Flag OFF: No episodic memory calls are made (byte-for-byte current behavior)."""
    masr_router_flag_off.episodic_memory = mock_episodic_memory

    with (
        patch.object(
            masr_router_flag_off.complexity_analyzer, "analyze"
        ) as mock_analyze,
        patch.object(masr_router_flag_off.cost_optimizer, "optimize") as mock_optimize,
    ):
        # Setup mocks
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
        )

        mock_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research", "analytics"],
            subtask_count=3,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 2},
            estimated_tokens=2000,
        )

        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )

        mock_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=2000,
                cost_per_request=0.002,
                total_monthly_cost=200.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="Balanced selection",
        )

        decision = await masr_router_flag_off.route("Test query", context={})

    # Assert: episodic memory was NOT queried
    mock_episodic_memory.retrieve_episodes.assert_not_called()

    # Assert: routing decision was made (current behavior preserved)
    assert decision.agent_allocation.worker_count >= 1


@pytest.mark.asyncio
async def test_flag_off_allocation_matches_analytic(
    masr_router_flag_off, mock_episodic_memory
):
    """Flag OFF: agent allocation is purely analytic (regression guard)."""
    masr_router_flag_off.episodic_memory = mock_episodic_memory

    with (
        patch.object(
            masr_router_flag_off.complexity_analyzer, "analyze"
        ) as mock_analyze,
        patch.object(masr_router_flag_off.cost_optimizer, "optimize") as mock_optimize,
    ):
        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
        )

        # PARALLEL mode with 2 domains -> analytic 3 workers (domains + 1),
        # which sits within the BALANCED-strategy PARALLEL budget cap (3) so
        # the assertion still measures the pure analytic value.
        mock_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research", "analytics"],
            subtask_count=4,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 2},
            estimated_tokens=2000,
        )

        mock_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=2000,
                cost_per_request=0.002,
                total_monthly_cost=200.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="Balanced",
        )

        decision = await masr_router_flag_off.route("Multi-domain query", context={})

    # Assert: analytic allocation (2 domains + 1 = 3); equals the budget cap
    # boundary so no clamping occurs and the value is purely analytic.
    assert decision.agent_allocation.worker_count == 3
    # No memory influence
    mock_episodic_memory.retrieve_episodes.assert_not_called()


# ============================================================================
# Feature Flag ON - Episodic Prior Tests
# ============================================================================


@pytest.mark.asyncio
async def test_flag_on_episodic_prior_nudges_allocation(
    masr_router_flag_on, mock_episodic_memory
):
    """Flag ON: episodic prior nudges worker_count within bounds."""
    # Seed episodic memory with 3 past episodes suggesting worker_count=6
    episodes = []
    for i in range(3):
        episodes.append(
            Episode(
                id=str(uuid.uuid4()),
                session_id=f"session-{i}",
                user_id="user-1",
                event_type=EventType.DECISION_MADE,
                event_data={"worker_count": 6},
                timestamp=datetime.now() - timedelta(days=i * 5),
                quality_score=0.9,
                tags=[],
            )
        )
    mock_episodic_memory.retrieve_episodes.return_value = episodes
    masr_router_flag_on.episodic_memory = mock_episodic_memory

    with (
        patch.object(
            masr_router_flag_on.complexity_analyzer, "analyze"
        ) as mock_analyze,
        patch.object(masr_router_flag_on.cost_optimizer, "optimize") as mock_optimize,
    ):
        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
        )

        # Analytic would allocate 4 (3 domains + 1)
        mock_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research", "analytics"],
            subtask_count=4,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 2},
            estimated_tokens=2000,
        )

        mock_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=2000,
                cost_per_request=0.002,
                total_monthly_cost=200.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="Balanced",
        )

        decision = await masr_router_flag_on.route(
            "Test query", context={}, strategy="quality_focused"
        )

    # Analytic: 4, prior: 6 → delta +2 (within cap of ±2) → adjusted to 4+2=6
    # But hard cap (max_parallel_workers default = 5) applies → final count is 5
    # Analytic = 3 (2 domains + 1); prior 6 -> memory-capped to 5; the
    # QUALITY_FOCUSED PARALLEL budget cap (4) then applies -> 4. The nudge
    # is visible (4 > 3) and both caps are exercised.
    assert decision.agent_allocation.worker_count == 4
    mock_episodic_memory.retrieve_episodes.assert_called_once()


@pytest.mark.asyncio
async def test_flag_on_cap_enforced(masr_router_flag_on, mock_episodic_memory):
    """Flag ON: cap prevents wild priors from moving worker_count too far."""
    # Prior suggests 20 workers (wild)
    episodes = [
        Episode(
            id=str(uuid.uuid4()),
            session_id="session-1",
            user_id="user-1",
            event_type=EventType.DECISION_MADE,
            event_data={"worker_count": 20},
            timestamp=datetime.now() - timedelta(days=1),
            quality_score=0.9,
            tags=[],
        )
    ]
    mock_episodic_memory.retrieve_episodes.return_value = episodes
    masr_router_flag_on.episodic_memory = mock_episodic_memory

    with (
        patch.object(
            masr_router_flag_on.complexity_analyzer, "analyze"
        ) as mock_analyze,
        patch.object(masr_router_flag_on.cost_optimizer, "optimize") as mock_optimize,
    ):
        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
        )

        # Analytic: 3
        mock_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research", "analytics"],
            subtask_count=3,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 2},
            estimated_tokens=2000,
        )

        mock_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=2000,
                cost_per_request=0.002,
                total_monthly_cost=200.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="Balanced",
        )

        decision = await masr_router_flag_on.route(
            "Test query", context={}, strategy="quality_focused"
        )

    # Analytic: 3, prior: 20 → delta +17, but cap is ±2 → adjusted to 3 + 2 = 5
    # Wild prior (20) -> memory cap limits to analytic+2 = 5; the
    # QUALITY_FOCUSED PARALLEL budget cap (4) is the final bound.
    assert decision.agent_allocation.worker_count == 4
    # Hard cap should also apply (max_parallel_workers default is 5)
    assert decision.agent_allocation.worker_count <= 5


@pytest.mark.asyncio
async def test_flag_on_cold_memory_graceful(masr_router_flag_on, mock_episodic_memory):
    """Flag ON, empty memory: graceful fallback to analytic path."""
    mock_episodic_memory.retrieve_episodes.return_value = []
    masr_router_flag_on.episodic_memory = mock_episodic_memory

    with (
        patch.object(
            masr_router_flag_on.complexity_analyzer, "analyze"
        ) as mock_analyze,
        patch.object(masr_router_flag_on.cost_optimizer, "optimize") as mock_optimize,
    ):
        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
        )

        mock_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research", "analytics"],
            subtask_count=3,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 2},
            estimated_tokens=2000,
        )

        mock_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=2000,
                cost_per_request=0.002,
                total_monthly_cost=200.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="Balanced",
        )

        decision = await masr_router_flag_on.route("Test query", context={})

    # Should fall back to analytic count (3)
    assert decision.agent_allocation.worker_count == 3


@pytest.mark.asyncio
async def test_flag_on_memory_raises_resilient(
    masr_router_flag_on, mock_episodic_memory
):
    """Flag ON, memory backend raises: request still routes (resilience)."""
    mock_episodic_memory.retrieve_episodes.side_effect = RuntimeError("DB down")
    masr_router_flag_on.episodic_memory = mock_episodic_memory

    with (
        patch.object(
            masr_router_flag_on.complexity_analyzer, "analyze"
        ) as mock_analyze,
        patch.object(masr_router_flag_on.cost_optimizer, "optimize") as mock_optimize,
    ):
        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
        )

        mock_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research"],
            subtask_count=2,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 1},
            estimated_tokens=1000,
        )

        mock_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=1000,
                cost_per_request=0.001,
                total_monthly_cost=100.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="Balanced",
        )

        # Should not raise; should route successfully
        decision = await masr_router_flag_on.route("Test query", context={})

    # Analytic path used (no crash)
    assert decision.agent_allocation.worker_count == 2


# ============================================================================
# Procedural Memory Context in Worker Prompts
# ============================================================================


@pytest.mark.asyncio
async def test_procedural_context_flag_off_no_injection():
    """Flag OFF: worker prompts do NOT include procedural memory context."""
    with patch("src.core.config.settings") as mock_settings:
        mock_settings.MEMORY_INFORMED_ROUTING_ENABLED = False

        worker = LLMWorkerAgentBase()
        worker.agent_type = "test_worker"
        worker.procedural_memory = Mock()  # Even if present, shouldn't be used

        task = AgentTask(
            id="task-1",
            agent_type="test_worker",
            priority=1,
            input_data={"query": "Test query", "domains": ["research"]},
        )

        # Mock _build_prompt
        worker._build_prompt = Mock(return_value="Base prompt")

        # Mock gemini service
        mock_gemini = Mock()
        mock_gemini.generate_content = AsyncMock(return_value="Result")
        worker.gemini_service = mock_gemini

        await worker.execute(task)

    # Assert: procedural memory was NOT queried
    worker.procedural_memory.retrieve_procedures.assert_not_called()
    # Prompt should NOT contain procedural context
    call_args = mock_gemini.generate_content.call_args[0][0]
    assert "Past Successful Approaches" not in call_args


@pytest.mark.asyncio
async def test_procedural_context_flag_on_with_seeded_memory():
    """Flag ON: worker prompts include procedural memory context (capped/truncated)."""
    with patch("src.core.config.settings") as mock_settings:
        mock_settings.MEMORY_INFORMED_ROUTING_ENABLED = True
        mock_settings.MEMORY_PROMPT_MAX_PROCEDURES = 3

        worker = LLMWorkerAgentBase()
        worker.agent_type = "test_worker"

        # Seed procedural memory
        mock_proc_mem = Mock()
        procedures = [
            Procedure(
                name="Strategy A",
                procedure_type=ProcedureType.STRATEGY,
                usage_count=10,
                success_count=9,
                confidence=0.85,
                parameters={"param1": "value1"},
            ),
            Procedure(
                name="Strategy B",
                procedure_type=ProcedureType.STRATEGY,
                usage_count=5,
                success_count=4,
                confidence=0.75,
                parameters={"param2": "value2"},
            ),
        ]
        mock_proc_mem.retrieve_procedures = AsyncMock(return_value=procedures)
        worker.procedural_memory = mock_proc_mem

        task = AgentTask(
            id="task-1",
            agent_type="test_worker",
            priority=1,
            input_data={"query": "Test query", "domains": ["research"]},
        )

        worker._build_prompt = Mock(return_value="Base prompt")

        mock_gemini = Mock()
        mock_gemini.generate_content = AsyncMock(return_value="Result")
        worker.gemini_service = mock_gemini

        await worker.execute(task)

    # Assert: procedural memory WAS queried
    mock_proc_mem.retrieve_procedures.assert_called_once()

    # Assert: prompt includes procedural context
    call_args = mock_gemini.generate_content.call_args[0][0]
    assert "Past Successful Approaches" in call_args
    assert "Strategy A" in call_args
    assert "confidence: 0.85" in call_args
    assert "success: 90.0%" in call_args


@pytest.mark.asyncio
async def test_procedural_context_cold_memory_graceful():
    """Flag ON, empty procedural memory: graceful no-op (no context block)."""
    with patch("src.core.config.settings") as mock_settings:
        mock_settings.MEMORY_INFORMED_ROUTING_ENABLED = True
        mock_settings.MEMORY_PROMPT_MAX_PROCEDURES = 3

        worker = LLMWorkerAgentBase()
        worker.agent_type = "test_worker"

        mock_proc_mem = Mock()
        mock_proc_mem.retrieve_procedures = AsyncMock(return_value=[])
        worker.procedural_memory = mock_proc_mem

        task = AgentTask(
            id="task-1",
            agent_type="test_worker",
            priority=1,
            input_data={"query": "Test query", "domains": ["research"]},
        )

        worker._build_prompt = Mock(return_value="Base prompt")

        mock_gemini = Mock()
        mock_gemini.generate_content = AsyncMock(return_value="Result")
        worker.gemini_service = mock_gemini

        await worker.execute(task)

    # Assert: prompt does NOT have procedural context (cold memory)
    call_args = mock_gemini.generate_content.call_args[0][0]
    assert "Past Successful Approaches" not in call_args


@pytest.mark.asyncio
async def test_procedural_context_raises_resilient():
    """Flag ON, procedural memory raises: worker still executes (resilience)."""
    with patch("src.core.config.settings") as mock_settings:
        mock_settings.MEMORY_INFORMED_ROUTING_ENABLED = True
        mock_settings.MEMORY_PROMPT_MAX_PROCEDURES = 3

        worker = LLMWorkerAgentBase()
        worker.agent_type = "test_worker"

        mock_proc_mem = Mock()
        mock_proc_mem.retrieve_procedures.side_effect = RuntimeError("Store down")
        worker.procedural_memory = mock_proc_mem

        task = AgentTask(
            id="task-1",
            agent_type="test_worker",
            priority=1,
            input_data={"query": "Test query", "domains": ["research"]},
        )

        worker._build_prompt = Mock(return_value="Base prompt")

        mock_gemini = Mock()
        mock_gemini.generate_content = AsyncMock(return_value="Result")
        worker.gemini_service = mock_gemini

        # Should not raise; should execute successfully
        result = await worker.execute(task)

    assert result.status == "success"
    assert result.output["content"] == "Result"
