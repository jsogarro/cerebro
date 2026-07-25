"""Focused attribution and evaluator-gated feedback-loop tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.ai_brain.experimentation.core.adaptive_allocation_engine import (
    AdaptiveAllocationEngine,
)
from src.ai_brain.router.adaptive_state_store import (
    InMemoryAdaptiveStateStore,
    StateLoadStatus,
)
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import (
    ComplexityAnalysis,
    ComplexityLevel,
    QueryDomain,
)
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS,
    ADAPTIVE_OUTCOME_MAX_AGE_SECONDS,
    ADAPTIVE_OUTCOME_RETENTION_SECONDS,
    ADAPTIVE_POLICY_VERSION,
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeApplicationStatus,
    OutcomeEligibilityReason,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import (
    AdaptiveAllocationProposal,
    AdaptiveRoutingStatus,
    CollaborationMode,
    RoutingStrategy,
)


def _policy() -> EvaluatorEligibilityPolicy:
    return EvaluatorEligibilityPolicy(
        allowed_evaluators={"quality-gate": frozenset({"2026-07-24"})}
    )


def _router(
    store: InMemoryAdaptiveStateStore | None = None,
    *,
    seed: int = 7,
    policy: EvaluatorEligibilityPolicy | None = None,
) -> MASRouter:
    return MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 0,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_performance_threshold": 0.0,
            "adaptive_routing_rng": np.random.default_rng(seed),
            "max_parallel": 5,
            "max_agents": 10,
        },
        adaptive_state_store=store,
        outcome_eligibility_policy=policy or _policy(),
    )


def _analysis() -> ComplexityAnalysis:
    return ComplexityAnalysis(
        level=ComplexityLevel.MODERATE,
        score=0.6,
        factors=None,  # type: ignore[arg-type]
        domains=[QueryDomain.RESEARCH, QueryDomain.ANALYTICS],
        estimated_tokens=2000,
        subtask_count=3,
        priority_level="normal",
    )


def _outcome(
    outcome_id: str,
    *,
    source: OutcomeSource = OutcomeSource.EVALUATOR,
    quality_availability: MetricAvailability = MetricAvailability.MEASURED,
    evaluator_name: str | None = "quality-gate",
    evaluator_version: str | None = "2026-07-24",
    policy_version: str = ADAPTIVE_POLICY_VERSION,
    applied_arm: int = 2,
) -> RoutingOutcome:
    quality = 0.9 if quality_availability == MetricAvailability.MEASURED else None
    if source != OutcomeSource.EVALUATOR:
        evaluator_name = None
        evaluator_version = None
    return RoutingOutcome(
        outcome_id=outcome_id,
        routing_id=f"route-{outcome_id}",
        policy_version=policy_version,
        source=source,
        collaboration_mode=CollaborationMode.PARALLEL,
        proposed_arm=2,
        applied_arm=applied_arm,
        final_worker_count=5,
        execution_status="completed",
        latency_ms=120,
        measured_cost=0.02,
        cost_availability=MetricAvailability.MEASURED,
        quality_score=quality,
        quality_availability=quality_availability,
        evaluator_name=evaluator_name,
        evaluator_version=evaluator_version,
    )


@pytest.mark.parametrize(
    "source",
    [
        OutcomeSource.HEURISTIC,
        OutcomeSource.FIXED,
        OutcomeSource.MANUAL,
        OutcomeSource.FIXTURE,
        OutcomeSource.ESTIMATED,
        OutcomeSource.UNAVAILABLE,
        OutcomeSource.MALFORMED,
        OutcomeSource.INCOMPATIBLE,
    ],
)
def test_non_evaluator_sources_are_ineligible(source: OutcomeSource) -> None:
    eligibility = _policy().assess(_outcome(source.value, source=source))
    assert eligibility.eligible is False
    assert eligibility.reason == OutcomeEligibilityReason.SOURCE_NOT_EVALUATOR


def test_unavailable_evaluator_quality_is_ineligible() -> None:
    eligibility = _policy().assess(
        _outcome(
            "unavailable",
            quality_availability=MetricAvailability.UNAVAILABLE,
        )
    )
    assert eligibility.reason == OutcomeEligibilityReason.QUALITY_NOT_MEASURED


def test_malformed_and_out_of_range_metrics_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        replace(_outcome("malformed"), quality_score=float("nan"))
    with pytest.raises(ValueError, match="accepted range"):
        replace(_outcome("range"), quality_score=1.1)


def test_wrong_evaluator_or_policy_version_is_ineligible() -> None:
    wrong_evaluator = _outcome("wrong-eval", evaluator_version="old")
    wrong_policy = _outcome("wrong-policy", policy_version="other-policy")
    assert (
        _policy().assess(wrong_evaluator).reason
        == OutcomeEligibilityReason.EVALUATOR_VERSION_NOT_ALLOWED
    )
    assert (
        _policy().assess(wrong_policy).reason
        == OutcomeEligibilityReason.POLICY_VERSION_MISMATCH
    )


def test_evaluator_outcome_older_than_marker_horizon_is_ineligible() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    policy = EvaluatorEligibilityPolicy(
        allowed_evaluators={"quality-gate": frozenset({"2026-07-24"})},
        clock=lambda: now,
    )
    stale = replace(
        _outcome("stale"),
        recorded_at=now - timedelta(days=7, seconds=1),
    )

    eligibility = policy.assess(stale)

    assert eligibility.eligible is False
    assert eligibility.reason == OutcomeEligibilityReason.OUTCOME_TOO_OLD


def test_evaluator_outcome_rejects_timestamp_beyond_allowed_clock_skew() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    policy = EvaluatorEligibilityPolicy(
        allowed_evaluators={"quality-gate": frozenset({"2026-07-24"})},
        clock=lambda: now,
    )
    within_skew = replace(
        _outcome("within-skew"),
        recorded_at=now
        + timedelta(seconds=ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS),
    )
    beyond_skew = replace(
        _outcome("beyond-skew"),
        recorded_at=now
        + timedelta(seconds=ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS + 1),
    )

    assert policy.assess(within_skew).eligible is True
    rejected = policy.assess(beyond_skew)
    assert rejected.eligible is False
    assert rejected.reason == OutcomeEligibilityReason.FUTURE_TIMESTAMP


def test_idempotency_retention_covers_age_horizon_and_positive_skew() -> None:
    assert ADAPTIVE_OUTCOME_RETENTION_SECONDS == (
        ADAPTIVE_OUTCOME_MAX_AGE_SECONDS + ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS
    )


@pytest.mark.asyncio
async def test_one_qualified_outcome_updates_literal_arm_once() -> None:
    store = InMemoryAdaptiveStateStore()
    router = _router(store)
    outcome = _outcome("opaque-1", applied_arm=2)

    first = await router.record_routing_outcome(outcome)
    after_first = await store.load()
    duplicate = await router.record_routing_outcome(outcome)
    after_duplicate = await store.load()

    assert first.status == OutcomeApplicationStatus.APPLIED
    assert first.learning_updated is True
    assert duplicate.status == OutcomeApplicationStatus.DUPLICATE
    assert duplicate.learning_updated is False
    assert duplicate.duplicate is True
    assert after_first.status == StateLoadStatus.LOADED
    assert after_first.snapshot is not None
    assert after_duplicate.snapshot == after_first.snapshot
    experiment = after_first.snapshot.experiments[0]
    assert experiment.ordered_arms == (-2, -1, 0, 1, 2)
    assert experiment.arm_counts == (0, 0, 0, 0, 1)


@pytest.mark.asyncio
async def test_replay_after_marker_horizon_cannot_learn_twice() -> None:
    clock = [datetime(2026, 7, 24, tzinfo=UTC)]
    policy = EvaluatorEligibilityPolicy(
        allowed_evaluators={"quality-gate": frozenset({"2026-07-24"})},
        clock=lambda: clock[0],
    )
    outcome = replace(_outcome("retained-outcome"), recorded_at=clock[0])
    first_store = InMemoryAdaptiveStateStore()

    first = await _router(first_store, policy=policy).record_routing_outcome(outcome)
    after_first = await first_store.load()
    assert after_first.snapshot is not None

    # A fresh store around the durable snapshot simulates an expired Redis
    # marker while retaining the aggregate state.
    replay_store = InMemoryAdaptiveStateStore(after_first.snapshot)
    clock[0] += timedelta(days=7, seconds=1)
    replay = await _router(replay_store, policy=policy).record_routing_outcome(outcome)
    after_replay = await replay_store.load()

    assert first.learning_updated is True
    assert replay.status == OutcomeApplicationStatus.INELIGIBLE_RECORDED
    assert replay.learning_updated is False
    assert replay.outcome.eligibility.reason == OutcomeEligibilityReason.OUTCOME_TOO_OLD
    assert after_replay.snapshot is not None
    assert after_replay.snapshot.eligible_outcome_count == 1
    assert (
        after_replay.snapshot.experiments[0].arm_counts
        == after_first.snapshot.experiments[0].arm_counts
    )


@pytest.mark.asyncio
async def test_concurrent_local_outcomes_are_serialized_without_loss() -> None:
    store = InMemoryAdaptiveStateStore()
    router = _router(store)

    results = await asyncio.gather(
        *(
            router.record_routing_outcome(_outcome(f"concurrent-{index}"))
            for index in range(20)
        )
    )
    loaded = await store.load()

    assert all(result.learning_updated for result in results)
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 20
    assert sum(loaded.snapshot.experiments[0].arm_counts) == 20


@pytest.mark.asyncio
async def test_ineligible_outcome_is_recorded_without_learning() -> None:
    store = InMemoryAdaptiveStateStore()
    result = await _router(store).record_routing_outcome(
        _outcome("manual-1", source=OutcomeSource.MANUAL)
    )
    loaded = await store.load()

    assert result.status == OutcomeApplicationStatus.INELIGIBLE_RECORDED
    assert result.learning_updated is False
    assert loaded.snapshot is not None
    assert loaded.snapshot.ineligible_outcome_count == 1
    assert loaded.snapshot.experiments == ()


@pytest.mark.asyncio
async def test_proposal_uses_real_memory_adjusted_baseline() -> None:
    router = _router(seed=11)
    proposal = await router._get_adaptive_allocation_adjustment(
        _analysis(),
        CollaborationMode.PARALLEL,
        episodic_prior=5,
    )
    assert proposal is not None
    assert proposal.analytic_baseline_count == 3
    assert proposal.memory_baseline_count == 5
    assert (
        proposal.proposed_worker_count
        == proposal.memory_baseline_count + proposal.proposed_arm
    )


@pytest.mark.asyncio
async def test_enabled_empty_runtime_is_effectively_cold_control() -> None:
    router = MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 5,
            "adaptive_routing_min_samples_per_arm": 1,
            "adaptive_routing_performance_threshold": 0.0,
            "max_parallel": 5,
            "max_agents": 10,
        },
        adaptive_state_store=InMemoryAdaptiveStateStore(),
        outcome_eligibility_policy=_policy(),
    )

    proposal = await router._get_adaptive_allocation_adjustment(
        _analysis(),
        CollaborationMode.PARALLEL,
        episodic_prior=None,
    )
    assert proposal is not None

    _, metadata = router._allocate_agents_with_attribution(
        _analysis(),
        CollaborationMode.PARALLEL,
        adaptive_recommendation=proposal,
        routing_strategy=RoutingStrategy.BALANCED,
    )

    assert proposal.ready is False
    assert proposal.proposed_arm == proposal.applied_arm == 0
    assert metadata.status is AdaptiveRoutingStatus.COLD
    assert metadata.enabled is True
    assert metadata.ready is False
    assert metadata.applied_arm == 0


def test_budget_clamp_executes_control_without_inferred_arm() -> None:
    router = _router()
    proposal = AdaptiveAllocationProposal(
        experiment_id="adaptive_allocation_parallel",
        analytic_baseline_count=3,
        memory_baseline_count=3,
        proposed_arm=2,
        proposed_worker_count=5,
        applied_arm=2,
        applied_worker_count=5,
        allocation_probability=0.6,
        ready=True,
        safety_check_passed=True,
    )
    allocation, metadata = router._allocate_agents_with_attribution(
        _analysis(),
        CollaborationMode.PARALLEL,
        adaptive_recommendation=proposal,
        routing_strategy=RoutingStrategy.COST_EFFICIENT,
    )

    assert metadata.proposed_arm == 2
    assert metadata.applied_arm == 0
    assert metadata.budget_clamped is True
    assert metadata.control_reason == "proposal_exceeds_strategy_budget"
    assert allocation.worker_count == metadata.final_worker_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_adjust", "expected_arm", "expected_count", "expected_arm_counts"),
    [
        (0, 0, 3, (0, 0, 1, 0, 0)),
        (1, 1, 4, (0, 0, 0, 1, 0)),
    ],
)
async def test_adjustment_cap_attributes_and_learns_the_executed_arm(
    max_adjust: int,
    expected_arm: int,
    expected_count: int,
    expected_arm_counts: tuple[int, int, int, int, int],
) -> None:
    store = InMemoryAdaptiveStateStore()
    router = _router(store)
    router.adaptive_routing_max_worker_adjust = max_adjust
    proposal = AdaptiveAllocationProposal(
        experiment_id="adaptive_allocation_parallel",
        analytic_baseline_count=3,
        memory_baseline_count=3,
        proposed_arm=2,
        proposed_worker_count=5,
        applied_arm=2,
        applied_worker_count=5,
        allocation_probability=0.6,
        ready=True,
        safety_check_passed=True,
    )

    allocation, metadata = router._allocate_agents_with_attribution(
        _analysis(),
        CollaborationMode.PARALLEL,
        adaptive_recommendation=proposal,
        routing_strategy=RoutingStrategy.QUALITY_FOCUSED,
    )
    outcome = replace(
        _outcome(f"cap-{max_adjust}"),
        applied_arm=metadata.applied_arm,
        final_worker_count=metadata.final_worker_count,
    )
    result = await router.record_routing_outcome(outcome)
    loaded = await store.load()

    assert allocation.worker_count == expected_count
    assert metadata.final_worker_count == expected_count
    assert metadata.applied_arm == expected_arm
    assert result.learning_updated is True
    assert loaded.snapshot is not None
    assert loaded.snapshot.experiments[0].arm_counts == expected_arm_counts


@pytest.mark.asyncio
async def test_injected_rng_makes_proposals_deterministic() -> None:
    first = await _router(seed=19)._get_adaptive_allocation_adjustment(
        _analysis(), CollaborationMode.PARALLEL, None
    )
    second = await _router(seed=19)._get_adaptive_allocation_adjustment(
        _analysis(), CollaborationMode.PARALLEL, None
    )
    assert first is not None and second is not None
    assert first.proposed_arm == second.proposed_arm
    assert first.allocation_probability == second.allocation_probability


@pytest.mark.asyncio
async def test_adaptive_route_never_reuses_a_cached_complete_decision() -> None:
    router = _router(seed=23)
    original = router._get_adaptive_allocation_adjustment
    adaptive_call = AsyncMock(wraps=original)
    router._get_adaptive_allocation_adjustment = adaptive_call  # type: ignore[method-assign]

    first = await router.route(
        "Compare the reliability tradeoffs of two distributed systems"
    )
    second = await router.route(
        "Compare the reliability tradeoffs of two distributed systems"
    )

    assert first.query_id != second.query_id
    assert first is not second
    assert adaptive_call.await_count == 2
    assert first.adaptive_metadata is not None
    assert second.adaptive_metadata is not None


@pytest.mark.asyncio
async def test_strong_thompson_arm_is_not_rejected_as_overallocated() -> None:
    engine = AdaptiveAllocationEngine(
        {
            "enable_safety": True,
            "rng": np.random.default_rng(29),
        }
    )
    router = _router()
    config = router._adaptive_allocation_config()
    await engine.register_experiment(
        "adaptive_allocation_parallel",
        list(config.initial_allocation),
        config,
    )
    bandit = engine.bandit_optimizers["adaptive_allocation_parallel"]
    bandit.arm_counts = [100, 100, 100, 100, 100]
    bandit.arm_reward_sums = [10.0, 20.0, 30.0, 40.0, 99.0]
    bandit.arm_values = [0.1, 0.2, 0.3, 0.4, 0.99]
    bandit.alpha_params = [11.0, 21.0, 31.0, 41.0, 100.0]
    bandit.beta_params = [91.0, 81.0, 71.0, 61.0, 2.0]

    decision = await engine.allocate_variant("adaptive_allocation_parallel")

    assert decision.proposed_variant_id == "+2"
    assert decision.variant_id == "+2"
    assert decision.allocation_probability is None
    assert decision.safety_check_passed is True
