"""Unit tests for evaluator-qualified advantage reward computation."""

from itertools import count

import pytest

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_POLICY_VERSION,
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import CollaborationMode

_OUTCOME_IDS = count()


@pytest.fixture
def router() -> MASRouter:
    """Create MASR with an explicit versioned evaluator allow-list."""

    return MASRouter(
        config={"adaptive_routing_enabled": True},
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"advantage-test": frozenset({"1"})}
        ),
    )


async def _record(
    router: MASRouter,
    mode: CollaborationMode,
    quality_score: float,
    actual_cost: float,
) -> None:
    outcome_number = next(_OUTCOME_IDS)
    outcome = RoutingOutcome(
        outcome_id=f"advantage-outcome-{outcome_number}",
        routing_id=f"advantage-route-{outcome_number}",
        policy_version=ADAPTIVE_POLICY_VERSION,
        source=OutcomeSource.EVALUATOR,
        collaboration_mode=mode,
        proposed_arm=0,
        applied_arm=0,
        final_worker_count=1,
        execution_status="completed",
        latency_ms=None,
        measured_cost=actual_cost,
        cost_availability=MetricAvailability.MEASURED,
        quality_score=quality_score,
        quality_availability=MetricAvailability.MEASURED,
        evaluator_name="advantage-test",
        evaluator_version="1",
    )
    result = await router.record_routing_outcome(outcome)
    assert result.learning_updated is True


@pytest.mark.asyncio
async def test_advantage_reward_first_query_initializes_baseline(
    router: MASRouter,
) -> None:
    """First evaluator outcome for a mode initializes baseline to 0.75."""

    await _record(router, CollaborationMode.PARALLEL, 0.80, 0.01)

    assert CollaborationMode.PARALLEL in router._mode_quality_baselines
    assert (
        abs(router._mode_quality_baselines[CollaborationMode.PARALLEL] - 0.7525) < 0.01
    )


@pytest.mark.asyncio
async def test_advantage_reward_above_baseline_positive(
    router: MASRouter,
) -> None:
    """Quality above baseline produces advantage above the reward midpoint."""

    router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] = 0.70
    await _record(router, CollaborationMode.HIERARCHICAL, 0.85, 0.02)

    assert (
        abs(router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] - 0.7075)
        < 0.01
    )


@pytest.mark.asyncio
async def test_advantage_reward_below_baseline_negative(
    router: MASRouter,
) -> None:
    """Quality below baseline produces advantage below the reward midpoint."""

    router._mode_quality_baselines[CollaborationMode.DIRECT] = 0.80
    await _record(router, CollaborationMode.DIRECT, 0.65, 0.01)

    assert abs(router._mode_quality_baselines[CollaborationMode.DIRECT] - 0.7925) < 0.01


@pytest.mark.asyncio
async def test_baseline_ema_converges(router: MASRouter) -> None:
    """Baseline EMA converges to repeated evaluator quality."""

    mode = CollaborationMode.PARALLEL
    router._mode_quality_baselines[mode] = 0.75
    for _ in range(100):
        await _record(router, mode, 0.85, 0.01)

    assert abs(router._mode_quality_baselines[mode] - 0.85) < 0.05


@pytest.mark.asyncio
async def test_advantage_reward_clamps_to_zero_one(router: MASRouter) -> None:
    """Extreme advantages remain valid continuous Beta rewards."""

    mode = CollaborationMode.ENSEMBLE
    router._mode_quality_baselines[mode] = 0.50
    await _record(router, mode, 0.95, 0.02)
    router._mode_quality_baselines[mode] = 0.90
    await _record(router, mode, 0.30, 0.01)

    assert mode in router._mode_quality_baselines


@pytest.mark.asyncio
async def test_advantage_isolates_mode_intrinsic_quality(
    router: MASRouter,
) -> None:
    """Similar within-mode advantages update different mode baselines equally."""

    router._mode_quality_baselines[CollaborationMode.DIRECT] = 0.85
    router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] = 0.70

    await _record(router, CollaborationMode.DIRECT, 0.87, 0.01)
    await _record(router, CollaborationMode.HIERARCHICAL, 0.72, 0.02)

    assert abs(router._mode_quality_baselines[CollaborationMode.DIRECT] - 0.851) < 0.01
    assert (
        abs(router._mode_quality_baselines[CollaborationMode.HIERARCHICAL] - 0.701)
        < 0.01
    )
