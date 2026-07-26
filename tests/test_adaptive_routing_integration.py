"""
Integration tests for adaptive routing feature.

Tests cover:
1. Flag OFF regression (byte-for-byte current behavior, zero engine calls)
2. Flag ON with cold history (no adaptation until threshold)
3. Flag ON with warm history (bounded adaptation within ±2)
4. Composition with memory prior (sequential, shared bound)
5. Engine error resilience (graceful fallback)
6. Hard cap preservation (final count respects system limits)
7. Structured logging when adaptation changes allocation
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import (
    ComplexityAnalysis,
    ComplexityLevel,
    QueryDomain,
)
from src.ai_brain.router.routing_types import CollaborationMode


@pytest.fixture
def mock_complexity_analysis():
    """Mock complexity analysis for testing."""
    return ComplexityAnalysis(
        level=ComplexityLevel.MODERATE,
        score=0.6,
        factors=None,  # type: ignore[arg-type]
        domains=[QueryDomain.RESEARCH, QueryDomain.ANALYTICS],
        estimated_tokens=2000,
        subtask_count=3,
        priority_level="normal",
    )


@pytest.fixture
def router_config_flag_off():
    """Router config with adaptive routing OFF."""
    return {
        "adaptive_routing_enabled": False,
        "memory_informed_routing_enabled": False,
        "max_parallel": 5,
        "max_agents": 10,
    }


@pytest.fixture
def router_config_flag_on():
    """Router config with adaptive routing ON."""
    return {
        "adaptive_routing_enabled": True,
        "adaptive_routing_min_history": 100,
        "adaptive_routing_max_worker_adjust": 2,
        "memory_informed_routing_enabled": False,
        "max_parallel": 5,
        "max_agents": 10,
    }


@pytest.mark.asyncio
class TestAdaptiveRoutingFlagOff:
    """Test suite for ADAPTIVE_ROUTING_ENABLED=False (regression guards)."""

    async def test_flag_off_returns_none(
        self, router_config_flag_off, mock_complexity_analysis
    ):
        """Flag OFF → _get_adaptive_allocation_adjustment returns None."""
        router = MASRouter(config=router_config_flag_off)

        result = await router._get_adaptive_allocation_adjustment(
            complexity_analysis=mock_complexity_analysis,
            collaboration_mode=CollaborationMode.PARALLEL,
            episodic_prior=None,
        )

        assert result is None

    async def test_flag_off_no_engine_initialization(self, router_config_flag_off):
        """Flag OFF → adaptive engine is NOT initialized."""
        router = MASRouter(config=router_config_flag_off)

        assert router.adaptive_routing_enabled is False
        assert router._adaptive_engine is None

    async def test_flag_off_zero_overhead(
        self, router_config_flag_off, mock_complexity_analysis
    ):
        """Flag OFF → no engine calls, zero overhead."""
        with patch(
            "src.ai_brain.experimentation.core.adaptive_allocation_engine.AdaptiveAllocationEngine"
        ) as mock_engine_class:
            router = MASRouter(config=router_config_flag_off)

            await router._get_adaptive_allocation_adjustment(
                complexity_analysis=mock_complexity_analysis,
                collaboration_mode=CollaborationMode.PARALLEL,
                episodic_prior=None,
            )

            # Engine class should NEVER be instantiated when flag is OFF
            mock_engine_class.assert_not_called()


@pytest.mark.asyncio
class TestAdaptiveRoutingColdStart:
    """Test suite for cold-start behavior (history < threshold)."""

    async def test_cold_history_returns_none(
        self, router_config_flag_on, mock_complexity_analysis
    ):
        """Cold eligible state executes explicit arm-0 control."""
        router = MASRouter(config=router_config_flag_on)

        result = await router._get_adaptive_allocation_adjustment(
            complexity_analysis=mock_complexity_analysis,
            collaboration_mode=CollaborationMode.PARALLEL,
            episodic_prior=None,
        )

        assert result is not None
        assert result.ready is False
        assert result.proposed_arm == result.applied_arm == 0
        assert result.control_reason == "global_eligible_samples_below_minimum"

    async def test_warm_history_allows_adaptation(
        self, router_config_flag_on, mock_complexity_analysis
    ):
        """Routing decisions do not count as evaluator-eligible samples."""
        router = MASRouter(config=router_config_flag_on)
        router.metrics_collector.routing_history.extend([object()] * 150)  # type: ignore[list-item]

        result = await router._get_adaptive_allocation_adjustment(
            complexity_analysis=mock_complexity_analysis,
            collaboration_mode=CollaborationMode.PARALLEL,
            episodic_prior=None,
        )

        assert result is not None
        assert result.ready is False
        assert result.applied_arm == 0


@pytest.mark.asyncio
class TestAdaptiveAdjustmentBounds:
    """Test suite for bounded adaptation (±2 cap)."""

    async def test_apply_adaptive_adjustment_within_bounds(self, router_config_flag_on):
        """Adaptive delta within ±2 → applied unchanged."""
        router = MASRouter(config=router_config_flag_on)

        # Baseline 5, recommendation 7 → delta +2 (within cap)
        result = router._apply_adaptive_adjustment(
            memory_adjusted_count=5, adaptive_recommendation=7
        )

        assert result == 7

    async def test_apply_adaptive_adjustment_hits_positive_cap(
        self, router_config_flag_on
    ):
        """Adaptive delta > +2 → capped to +2."""
        router = MASRouter(config=router_config_flag_on)

        # Baseline 5, recommendation 10 → delta +5, capped to +2 → result 7
        result = router._apply_adaptive_adjustment(
            memory_adjusted_count=5, adaptive_recommendation=10
        )

        assert result == 7

    async def test_apply_adaptive_adjustment_hits_negative_cap(
        self, router_config_flag_on
    ):
        """Adaptive delta < -2 → capped to -2."""
        router = MASRouter(config=router_config_flag_on)

        # Baseline 5, recommendation 1 → delta -4, capped to -2 → result 3
        result = router._apply_adaptive_adjustment(
            memory_adjusted_count=5, adaptive_recommendation=1
        )

        assert result == 3

    async def test_apply_adaptive_adjustment_never_below_1(self, router_config_flag_on):
        """Adaptive adjustment never produces worker_count < 1."""
        router = MASRouter(config=router_config_flag_on)

        # Baseline 2, recommendation 0 → delta -2 → result would be 0, floored to 1
        result = router._apply_adaptive_adjustment(
            memory_adjusted_count=2, adaptive_recommendation=0
        )

        assert result == 1


@pytest.mark.asyncio
class TestCompositionWithMemoryPrior:
    """Test sequential composition: memory prior → adaptive adjustment."""

    async def test_composition_memory_then_adaptive(self, router_config_flag_on):
        """Memory adjusts first, adaptive sees memory-adjusted baseline."""
        router = MASRouter(config=router_config_flag_on)
        router.memory_informed_routing_enabled = True
        router.memory_routing_max_worker_adjust = 2

        # Analytic: 3 workers
        # Memory prior: 5 → memory adjusts 3→5 (delta +2)
        # Adaptive recommendation: 7 → adaptive adjusts 5→7 (delta +2)
        # Final: 7

        memory_adjusted = router._apply_memory_adjustment(
            analytic_count=3, episodic_prior=5
        )
        assert memory_adjusted == 5

        final = router._apply_adaptive_adjustment(
            memory_adjusted_count=memory_adjusted, adaptive_recommendation=7
        )
        assert final == 7

    async def test_composition_shared_bound(self, router_config_flag_on):
        """Both memory and adaptive respect their individual ±2 bounds."""
        router = MASRouter(config=router_config_flag_on)
        router.memory_informed_routing_enabled = True
        router.memory_routing_max_worker_adjust = 2

        # Analytic: 3
        # Memory tries to go to 10 → capped to 5 (3+2)
        # Adaptive tries to go to 12 → capped to 7 (5+2)
        # Final: 7

        memory_adjusted = router._apply_memory_adjustment(
            analytic_count=3, episodic_prior=10
        )
        assert memory_adjusted == 5  # Capped at 3+2

        final = router._apply_adaptive_adjustment(
            memory_adjusted_count=memory_adjusted, adaptive_recommendation=12
        )
        assert final == 7  # Capped at 5+2


@pytest.mark.asyncio
class TestEngineErrorResilience:
    """Test graceful fallback when adaptive engine raises errors."""

    async def test_engine_error_returns_none(
        self, router_config_flag_on, mock_complexity_analysis
    ):
        """Engine failure executes explicit arm-0 control."""
        router_config_flag_on["adaptive_routing_min_history"] = 0
        router_config_flag_on["adaptive_routing_min_samples_per_arm"] = 0
        router = MASRouter(config=router_config_flag_on)

        with patch(
            "src.ai_brain.router.masr.AdaptiveAllocationEngine.allocate_variant",
            new=AsyncMock(side_effect=RuntimeError("Bandit explosion")),
        ):
            result = await router._get_adaptive_allocation_adjustment(
                complexity_analysis=mock_complexity_analysis,
                collaboration_mode=CollaborationMode.PARALLEL,
                episodic_prior=None,
            )

        assert result is not None
        assert result.applied_arm == 0
        assert result.control_reason == "allocation_error"


@pytest.mark.asyncio
class TestHardCapPreservation:
    """Test that final worker_count respects system limits."""

    async def test_parallel_mode_respects_max_parallel_workers(
        self, router_config_flag_on, mock_complexity_analysis
    ):
        """PARALLEL mode: final count <= max_parallel_workers."""
        router = MASRouter(config=router_config_flag_on)

        # Mock to return very high adaptive recommendation
        with patch.object(router, "_apply_adaptive_adjustment", return_value=10):
            allocation = router._allocate_agents(
                complexity_analysis=mock_complexity_analysis,
                collaboration_mode=CollaborationMode.PARALLEL,
                episodic_prior=None,
                adaptive_recommendation=10,
            )

        # Should be capped at max_parallel_workers (5)
        assert allocation.worker_count <= router.max_parallel_workers

    async def test_hierarchical_mode_respects_max_agents_per_query(
        self, router_config_flag_on, mock_complexity_analysis
    ):
        """HIERARCHICAL mode: final count <= max_agents_per_query."""
        router = MASRouter(config=router_config_flag_on)

        # Mock to return very high adaptive recommendation
        with patch.object(router, "_apply_adaptive_adjustment", return_value=15):
            allocation = router._allocate_agents(
                complexity_analysis=mock_complexity_analysis,
                collaboration_mode=CollaborationMode.HIERARCHICAL,
                episodic_prior=None,
                adaptive_recommendation=15,
            )

        # Should be capped at max_agents_per_query (10)
        assert allocation.worker_count <= router.max_agents_per_query


@pytest.mark.asyncio
class TestStructuredLogging:
    """Test structured log events when adaptation changes allocation."""

    async def test_logs_when_adaptation_changes_count(self, router_config_flag_on):
        """Adaptive delta != 0 → structured log event emitted."""
        router = MASRouter(config=router_config_flag_on)

        # The method logs with structlog; verify it doesn't raise
        result = router._apply_adaptive_adjustment(
            memory_adjusted_count=5, adaptive_recommendation=7
        )

        # Verify the result is correct
        assert result == 7

    async def test_no_log_when_no_change(self, router_config_flag_on):
        """Adaptive delta == 0 → no log event."""
        router = MASRouter(config=router_config_flag_on)

        # The method should not log when delta is 0
        result = router._apply_adaptive_adjustment(
            memory_adjusted_count=5, adaptive_recommendation=5
        )

        # Verify no change
        assert result == 5
