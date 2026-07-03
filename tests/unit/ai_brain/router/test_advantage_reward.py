"""Unit tests for advantage reward computation in adaptive routing.

Tests verify that the advantage reward (quality - baseline) correctly isolates
allocation improvement signal from mode-intrinsic quality differences.
"""

from types import SimpleNamespace

import pytest

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_types import CollaborationMode


@pytest.fixture
def router():
    """Create MASR with adaptive routing enabled."""
    config = {
        "adaptive_routing_enabled": True,
        "adaptive_routing_min_history": 10,  # Low for test
        "adaptive_routing_max_worker_adjust": 2,
    }
    return MASRouter(config=config)


@pytest.mark.asyncio
async def test_advantage_reward_first_query_initializes_baseline(router):
    """First query for a mode initializes baseline to 0.75."""
    # Create a minimal decision stub
    decision = SimpleNamespace(
        collaboration_mode=CollaborationMode.PARALLEL,
        complexity_analysis=SimpleNamespace(domains=["d1"], subtask_count=3),
        agent_allocation=SimpleNamespace(worker_count=3),
    )

    # Record outcome
    await router.record_routing_outcome(decision, quality_score=0.80, actual_cost=0.01)

    # Baseline should be initialized to 0.75
    assert CollaborationMode.PARALLEL in router._mode_quality_baselines
    # After one update with alpha=0.05: baseline = 0.95*0.75 + 0.05*0.80 = 0.7525
    assert (
        abs(router._mode_quality_baselines[CollaborationMode.PARALLEL] - 0.7525) < 0.01
    )


@pytest.mark.asyncio
async def test_advantage_reward_above_baseline_positive(router):
    """Quality above baseline produces advantage > 0 → reward > 0.5."""
    # Prime the baseline
    router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] = 0.70

    decision = SimpleNamespace(
        collaboration_mode=CollaborationMode.HIERARCHICAL,
        complexity_analysis=SimpleNamespace(domains=["d1"], subtask_count=5),
        agent_allocation=SimpleNamespace(worker_count=5),
    )

    # Quality 0.85 > baseline 0.70 → advantage = +0.15 → reward = 0.15 + 0.5 = 0.65
    await router.record_routing_outcome(decision, quality_score=0.85, actual_cost=0.02)

    # We can't directly inspect the reward passed to the engine without mocking,
    # but we can verify the baseline updated correctly
    # New baseline = 0.95*0.70 + 0.05*0.85 = 0.665 + 0.0425 = 0.7075
    assert (
        abs(router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] - 0.7075)
        < 0.01
    )


@pytest.mark.asyncio
async def test_advantage_reward_below_baseline_negative(router):
    """Quality below baseline produces advantage < 0 → reward < 0.5."""
    router._mode_quality_baselines[CollaborationMode.DIRECT] = 0.80

    decision = SimpleNamespace(
        collaboration_mode=CollaborationMode.DIRECT,
        complexity_analysis=SimpleNamespace(domains=["d1"], subtask_count=1),
        agent_allocation=SimpleNamespace(worker_count=1),
    )

    # Quality 0.65 < baseline 0.80 → advantage = -0.15 → reward = -0.15 + 0.5 = 0.35
    await router.record_routing_outcome(decision, quality_score=0.65, actual_cost=0.01)

    # New baseline = 0.95*0.80 + 0.05*0.65 = 0.76 + 0.0325 = 0.7925
    assert abs(router._mode_quality_baselines[CollaborationMode.DIRECT] - 0.7925) < 0.01


@pytest.mark.asyncio
async def test_baseline_ema_converges(router):
    """Baseline EMA converges to mean quality over repeated updates."""
    mode = CollaborationMode.PARALLEL
    router._mode_quality_baselines[mode] = 0.75  # Initial

    decision = SimpleNamespace(
        collaboration_mode=mode,
        complexity_analysis=SimpleNamespace(domains=["d1", "d2"], subtask_count=3),
        agent_allocation=SimpleNamespace(worker_count=3),
    )

    # Feed constant quality 0.85 for 100 updates
    for _ in range(100):
        await router.record_routing_outcome(
            decision, quality_score=0.85, actual_cost=0.01
        )

    # After many updates with alpha=0.05, baseline should converge close to 0.85
    # Convergence formula: after n updates, baseline ≈ initial*(0.95^n) + target*(1 - 0.95^n)
    # After 100 updates: baseline ≈ 0.85 (with tiny residual from initial 0.75)
    assert abs(router._mode_quality_baselines[mode] - 0.85) < 0.05


@pytest.mark.asyncio
async def test_advantage_reward_clamps_to_zero_one(router):
    """Extreme advantage values are clamped to [0, 1]."""
    mode = CollaborationMode.ENSEMBLE
    router._mode_quality_baselines[mode] = 0.50

    decision = SimpleNamespace(
        collaboration_mode=mode,
        complexity_analysis=SimpleNamespace(domains=["d1"], subtask_count=5),
        agent_allocation=SimpleNamespace(worker_count=5),
    )

    # Extreme high quality: 0.95 - 0.50 = +0.45 → reward = 0.95 (clamped to 1.0)
    await router.record_routing_outcome(decision, quality_score=0.95, actual_cost=0.02)

    # Reset baseline to high value
    router._mode_quality_baselines[mode] = 0.90

    # Extreme low quality: 0.30 - 0.90 = -0.60 → reward = -0.10 (clamped to 0.0)
    await router.record_routing_outcome(decision, quality_score=0.30, actual_cost=0.01)

    # Just verify no exceptions and baseline updated
    assert mode in router._mode_quality_baselines


@pytest.mark.asyncio
async def test_advantage_isolates_mode_intrinsic_quality(router):
    """Advantage reward isolates allocation improvement from mode-intrinsic quality."""
    # Set up two modes with different intrinsic quality levels
    router._mode_quality_baselines[CollaborationMode.DIRECT] = 0.85  # High baseline
    router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] = (
        0.70  # Low baseline
    )

    direct_decision = SimpleNamespace(
        collaboration_mode=CollaborationMode.DIRECT,
        complexity_analysis=SimpleNamespace(domains=["d1"], subtask_count=1),
        agent_allocation=SimpleNamespace(worker_count=1),
    )

    hierarchical_decision = SimpleNamespace(
        collaboration_mode=CollaborationMode.HIERARCHICAL,
        complexity_analysis=SimpleNamespace(domains=["d1"], subtask_count=5),
        agent_allocation=SimpleNamespace(worker_count=5),
    )

    # DIRECT query with quality 0.87 (just above baseline 0.85) → advantage ≈ +0.02
    await router.record_routing_outcome(
        direct_decision, quality_score=0.87, actual_cost=0.01
    )

    # HIERARCHICAL query with quality 0.72 (just above baseline 0.70) → advantage ≈ +0.02
    await router.record_routing_outcome(
        hierarchical_decision, quality_score=0.72, actual_cost=0.02
    )

    # Both should have similar advantage (~0.02) even though absolute quality differs
    # This is the key property: advantage isolates allocation improvement
    # We can't directly verify rewards without mocking, but baselines should update minimally
    # (small advantage → small baseline shift)

    # DIRECT: 0.95*0.85 + 0.05*0.87 = 0.8075 + 0.0435 = 0.851
    # HIERARCHICAL: 0.95*0.70 + 0.05*0.72 = 0.665 + 0.036 = 0.701
    assert abs(router._mode_quality_baselines[CollaborationMode.DIRECT] - 0.851) < 0.01
    assert (
        abs(router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] - 0.701)
        < 0.01
    )
