"""Unit tests for dark adaptive outcome recording in MASRouter and RoutingOutcomeRecorder."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pytest

from src.ai_brain.router.adaptive_state_store import InMemoryAdaptiveStateStore
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.outcome_recorder import (
    ExecutedAllocationOutcome,
    RecordedRoutingDecision,
    RoutingOutcomeRecorder,
)
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_POLICY_VERSION,
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeApplicationStatus,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import CollaborationMode


def _dark_router(
    store: InMemoryAdaptiveStateStore | None = None,
    *,
    fixture_mode: bool = False,
) -> MASRouter:
    return MASRouter(
        config={
            "adaptive_routing_enabled": False,
            "adaptive_routing_min_history": 0,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_performance_threshold": 0.0,
            "adaptive_routing_rng": np.random.default_rng(13),
            "enable_caching": False,
            "fixture_mode": fixture_mode,
        },
        adaptive_state_store=store or InMemoryAdaptiveStateStore(),
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"claims.coverage": frozenset({"1.0.0"})}
        ),
    )


def _decision_fingerprint(decision: Any) -> dict[str, Any]:
    raw = asdict(decision)
    raw.pop("query_id", None)
    raw.pop("timestamp", None)
    return raw


@pytest.mark.asyncio
async def test_dark_mode_records_eligible_outcome_and_updates_bandit_state() -> None:
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store)
    recorder = RoutingOutcomeRecorder(router, retry_delay_seconds=0)

    decision = RecordedRoutingDecision.from_allocation(
        collaboration_mode=CollaborationMode.PARALLEL,
        worker_count=3,
    )
    result = await recorder.record(
        decision,
        ExecutedAllocationOutcome(
            execution_id="run-dark-1",
            allocation_key="primary",
            allocation_attempt_id="terminal",
            execution_status="succeeded",
            latency_ms=250,
            source=OutcomeSource.EVALUATOR,
            measured_cost=0.04,
            cost_availability=MetricAvailability.MEASURED,
            quality_score=0.92,
            quality_availability=MetricAvailability.MEASURED,
            evaluator_name="claims.coverage",
            evaluator_version="1.0.0",
            evaluation_id="eval-row-1",
            run_id="run-dark-1",
        ),
    )

    loaded = await store.load()
    status = await router.get_adaptive_status()

    assert result.status is OutcomeApplicationStatus.APPLIED
    assert result.learning_updated is True
    assert result.outcome.evaluator_name == "claims.coverage"
    assert result.outcome.evaluator_version == "1.0.0"
    assert result.outcome.evaluation_id == "eval-row-1"
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 1
    assert loaded.snapshot.processed_outcome_count == 1
    assert loaded.snapshot.experiments[0].arm_counts == (0, 0, 1, 0, 0)
    assert status["enabled"] is False
    assert status["effective_state"] == "disabled"
    assert status["eligible_outcome_count"] == 1


@pytest.mark.asyncio
async def test_dark_bandit_learning_leaves_routing_decisions_byte_identical() -> None:
    query = "Compare macroeconomic and fiscal policy tradeoffs across two regions"
    baseline_router = _dark_router()
    trained_store = InMemoryAdaptiveStateStore()
    trained_router = _dark_router(trained_store)
    recorder = RoutingOutcomeRecorder(trained_router, retry_delay_seconds=0)

    decision_before = await trained_router.route(query)
    baseline_decision = await baseline_router.route(query)

    for index in range(10):
        res = await recorder.record(
            RecordedRoutingDecision.from_allocation(
                collaboration_mode=decision_before.collaboration_mode,
                worker_count=decision_before.agent_allocation.worker_count,
                proposed_arm=2,
                applied_arm=2,
            ),
            ExecutedAllocationOutcome(
                execution_id=f"run-train-{index}",
                allocation_key="primary",
                allocation_attempt_id="terminal",
                execution_status="succeeded",
                latency_ms=100,
                source=OutcomeSource.EVALUATOR,
                measured_cost=None,
                cost_availability=MetricAvailability.UNAVAILABLE,
                quality_score=0.99,
                quality_availability=MetricAvailability.MEASURED,
                evaluator_name="claims.coverage",
                evaluator_version="1.0.0",
                evaluation_id=f"eval-{index}",
                run_id=f"run-train-{index}",
            ),
        )
        assert res.learning_updated is True

    decision_after = await trained_router.route(query)
    status = await trained_router.get_adaptive_status()

    assert status["eligible_outcome_count"] == 10
    assert status["enabled"] is False
    assert _decision_fingerprint(decision_before) == _decision_fingerprint(
        baseline_decision
    )
    assert _decision_fingerprint(decision_after) == _decision_fingerprint(
        baseline_decision
    )


@pytest.mark.asyncio
async def test_dark_mode_records_failed_run_as_ineligible_without_updating_bandit() -> (
    None
):
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store)
    recorder = RoutingOutcomeRecorder(router, retry_delay_seconds=0)

    result = await recorder.record(
        RecordedRoutingDecision.from_allocation(
            collaboration_mode=CollaborationMode.HIERARCHICAL,
            worker_count=2,
        ),
        ExecutedAllocationOutcome(
            execution_id="run-failed-1",
            allocation_key="primary",
            allocation_attempt_id="terminal",
            execution_status="failed",
            latency_ms=None,
            source=OutcomeSource.UNAVAILABLE,
            measured_cost=None,
            cost_availability=MetricAvailability.UNAVAILABLE,
            quality_score=None,
            quality_availability=MetricAvailability.UNAVAILABLE,
            run_id="run-failed-1",
        ),
    )

    loaded = await store.load()
    assert result.status is OutcomeApplicationStatus.INELIGIBLE_RECORDED
    assert result.learning_updated is False
    assert result.outcome.execution_status == "failed"
    assert result.outcome.measured_cost is None
    assert result.outcome.cost_availability is MetricAvailability.UNAVAILABLE
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 0
    assert loaded.snapshot.ineligible_outcome_count == 1
    assert loaded.snapshot.processed_outcome_count == 1
    assert loaded.snapshot.experiments == ()


@pytest.mark.asyncio
async def test_fixture_mode_router_refuses_dark_learning() -> None:
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store, fixture_mode=True)
    outcome = RoutingOutcome(
        outcome_id="out-fixture-1",
        routing_id="rt-fixture-1",
        policy_version=ADAPTIVE_POLICY_VERSION,
        source=OutcomeSource.EVALUATOR,
        collaboration_mode=CollaborationMode.PARALLEL,
        proposed_arm=0,
        applied_arm=0,
        final_worker_count=2,
        execution_status="succeeded",
        latency_ms=50,
        measured_cost=None,
        cost_availability=MetricAvailability.UNAVAILABLE,
        quality_score=0.9,
        quality_availability=MetricAvailability.MEASURED,
        evaluator_name="claims.coverage",
        evaluator_version="1.0.0",
    )

    result = await router.record_routing_outcome(outcome)

    assert result.status is OutcomeApplicationStatus.INELIGIBLE_RECORDED
    assert result.learning_updated is False
    assert result.reason == "fixture_mode_disabled"
