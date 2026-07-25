"""Focused snapshot, state-store, conflict, and degradation tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from src.ai_brain.router.adaptive_state_store import (
    AdaptiveExperimentSnapshot,
    AdaptiveStateSnapshot,
    InMemoryAdaptiveStateStore,
    RedisAdaptiveStateStore,
    StateLoadResult,
    StateLoadStatus,
    StateWriteResult,
    StateWriteStatus,
    empty_adaptive_snapshot,
)
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import (
    ComplexityAnalysis,
    ComplexityLevel,
    QueryDomain,
)
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_OUTCOME_RETENTION_SECONDS,
    ADAPTIVE_POLICY_VERSION,
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeApplicationStatus,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import CollaborationMode


def _experiment() -> AdaptiveExperimentSnapshot:
    return AdaptiveExperimentSnapshot(
        experiment_id="adaptive_allocation_parallel",
        ordered_arms=(-2, -1, 0, 1, 2),
        arm_counts=(1, 2, 3, 4, 5),
        arm_reward_sums=(0.4, 1.1, 2.1, 3.2, 4.5),
        arm_values=(0.4, 0.55, 0.7, 0.8, 0.9),
        alpha_params=(1.4, 2.1, 3.1, 4.2, 5.5),
        beta_params=(1.6, 1.9, 1.9, 1.8, 1.5),
    )


def _snapshot(revision: int = 1) -> AdaptiveStateSnapshot:
    return AdaptiveStateSnapshot(
        revision=revision,
        experiments=(_experiment(),),
        mode_quality_baselines=(("parallel", 0.81),),
        eligible_outcome_count=15,
        processed_outcome_count=15,
    )


def _analysis() -> ComplexityAnalysis:
    return ComplexityAnalysis(
        level=ComplexityLevel.MODERATE,
        score=0.6,
        factors=None,  # type: ignore[arg-type]
        domains=[QueryDomain.RESEARCH, QueryDomain.ANALYTICS],
        estimated_tokens=1000,
        subtask_count=3,
        priority_level="normal",
    )


def _outcome(outcome_id: str = "state-outcome") -> RoutingOutcome:
    return RoutingOutcome(
        outcome_id=outcome_id,
        routing_id=f"route-{outcome_id}",
        policy_version=ADAPTIVE_POLICY_VERSION,
        source=OutcomeSource.EVALUATOR,
        collaboration_mode=CollaborationMode.PARALLEL,
        proposed_arm=1,
        applied_arm=1,
        final_worker_count=4,
        execution_status="completed",
        latency_ms=100,
        measured_cost=None,
        cost_availability=MetricAvailability.UNAVAILABLE,
        quality_score=0.88,
        quality_availability=MetricAvailability.MEASURED,
        evaluator_name="quality-gate",
        evaluator_version="1",
    )


def _router(store: Any) -> MASRouter:
    return MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 0,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_performance_threshold": 0.0,
            "adaptive_routing_conflict_retries": 2,
            "adaptive_routing_conflict_backoff_seconds": 0.0,
        },
        adaptive_state_store=store,
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"quality-gate": frozenset({"1"})}
        ),
    )


def test_snapshot_round_trip_is_strict_and_data_minimized() -> None:
    snapshot = _snapshot()
    payload = snapshot.to_json()
    restored = AdaptiveStateSnapshot.from_json(payload)

    assert restored == snapshot
    lowered = payload.lower()
    for forbidden in (
        "query",
        "prompt",
        "output",
        "user_id",
        "provider",
        "token",
        "secret",
    ):
        assert forbidden not in lowered

    with pytest.raises(ValueError, match="fields mismatch"):
        AdaptiveStateSnapshot.from_dict(
            {**json.loads(payload), "query": "must never persist"}
        )


def test_snapshot_rejects_corrupt_and_incompatible_statistics() -> None:
    with pytest.raises(ValueError, match="arm reward sum"):
        replace(
            _experiment(),
            arm_counts=(0, 0, 0, 0, 0),
            arm_reward_sums=(1.0, 0.0, 0.0, 0.0, 0.0),
        )
    with pytest.raises(ValueError, match="ordered_arms"):
        replace(_experiment(), ordered_arms=(0, -2, -1, 1, 2))


@pytest.mark.asyncio
async def test_in_memory_compare_and_set_is_atomic_and_idempotent() -> None:
    store = InMemoryAdaptiveStateStore()
    next_state = empty_adaptive_snapshot().next_revision(
        processed_outcome_count=1,
        ineligible_outcome_count=1,
    )

    applied = await store.compare_and_set(
        expected_revision=0,
        snapshot=next_state,
        outcome_id="opaque-1",
    )
    duplicate = await store.compare_and_set(
        expected_revision=1,
        snapshot=next_state.next_revision(
            processed_outcome_count=2,
            ineligible_outcome_count=2,
        ),
        outcome_id="opaque-1",
    )
    conflict = await store.compare_and_set(
        expected_revision=0,
        snapshot=next_state,
        outcome_id="opaque-2",
    )

    assert applied.status == StateWriteStatus.APPLIED
    assert duplicate.status == StateWriteStatus.DUPLICATE
    assert conflict.status == StateWriteStatus.CONFLICT
    assert (await store.load()).snapshot == next_state


class _FakeRedis:
    def __init__(
        self,
        *,
        payload: str | bytes | None = None,
        eval_result: int = 1,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.eval_result = eval_result
        self.error = error
        self.eval_calls: list[tuple[Any, ...]] = []

    async def get(self, key: str) -> str | bytes | None:
        if self.error:
            raise self.error
        return self.payload

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        if self.error:
            raise self.error
        self.eval_calls.append((script, numkeys, *keys_and_args))
        return self.eval_result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("redis_result", "expected"),
    [
        (1, StateWriteStatus.APPLIED),
        (2, StateWriteStatus.DUPLICATE),
        (0, StateWriteStatus.CONFLICT),
        (-1, StateWriteStatus.CORRUPT),
    ],
)
async def test_redis_compare_and_set_maps_atomic_results(
    redis_result: int, expected: StateWriteStatus
) -> None:
    client = _FakeRedis(eval_result=redis_result)
    store = RedisAdaptiveStateStore(client)
    state = empty_adaptive_snapshot().next_revision(
        processed_outcome_count=1,
        ineligible_outcome_count=1,
    )
    result = await store.compare_and_set(
        expected_revision=0,
        snapshot=state,
        outcome_id="opaque-redis",
    )

    assert result.status == expected
    assert client.eval_calls
    script, numkeys, state_key, outcome_key, *arguments = client.eval_calls[0]
    assert numkeys == 2
    assert 'redis.call("EXISTS", KEYS[2])' in script
    assert '"EX", ARGV[3]' in script
    assert state_key.endswith(":state")
    assert ":outcome:" in outcome_key
    assert "{1:masr-adaptive-v1}" in state_key
    assert "{1:masr-adaptive-v1}" in outcome_key
    assert arguments[-1] == str(ADAPTIVE_OUTCOME_RETENTION_SECONDS)


def test_redis_outcome_markers_are_hashed_cluster_local_and_expiring() -> None:
    store = RedisAdaptiveStateStore(
        _FakeRedis(),
        schema_version="schema-2",
        policy_version="policy-2",
        idempotency_retention_seconds=3600,
    )

    first = store.outcome_key("opaque-outcome")
    duplicate = store.outcome_key("opaque-outcome")
    second = store.outcome_key("different-outcome")

    assert first == duplicate
    assert first != second
    assert "opaque-outcome" not in first
    assert "{schema-2:policy-2}" in store.state_key
    assert "{schema-2:policy-2}" in first
    assert store.idempotency_retention_seconds == 3600

    with pytest.raises(ValueError, match="must be positive"):
        RedisAdaptiveStateStore(_FakeRedis(), idempotency_retention_seconds=0)


@pytest.mark.asyncio
async def test_redis_load_reports_corrupt_incompatible_and_error() -> None:
    corrupt = await RedisAdaptiveStateStore(_FakeRedis(payload="{not-json")).load()
    incompatible_payload = replace(
        _snapshot(), policy_version="different-policy"
    ).to_json()
    incompatible = await RedisAdaptiveStateStore(
        _FakeRedis(payload=incompatible_payload)
    ).load()
    failed = await RedisAdaptiveStateStore(
        _FakeRedis(error=ConnectionError("redis unavailable"))
    ).load()

    assert corrupt.status == StateLoadStatus.CORRUPT
    assert incompatible.status == StateLoadStatus.INCOMPATIBLE
    assert failed.status == StateLoadStatus.ERROR


class _ConflictOnceStore:
    def __init__(self) -> None:
        self.delegate = InMemoryAdaptiveStateStore()
        self.calls = 0

    async def load(self) -> StateLoadResult:
        return await self.delegate.load()

    async def compare_and_set(
        self,
        *,
        expected_revision: int,
        snapshot: AdaptiveStateSnapshot,
        outcome_id: str,
    ) -> StateWriteResult:
        self.calls += 1
        if self.calls == 1:
            return StateWriteResult(StateWriteStatus.CONFLICT)
        return await self.delegate.compare_and_set(
            expected_revision=expected_revision,
            snapshot=snapshot,
            outcome_id=outcome_id,
        )


@pytest.mark.asyncio
async def test_router_reloads_and_reapplies_after_bounded_conflict() -> None:
    store = _ConflictOnceStore()
    result = await _router(store).record_routing_outcome(_outcome())

    assert result.status == OutcomeApplicationStatus.APPLIED
    assert result.learning_updated is True
    assert store.calls == 2
    loaded = await store.load()
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 1


class _FailedStore:
    async def load(self) -> StateLoadResult:
        return StateLoadResult(StateLoadStatus.ERROR, reason="unavailable")

    async def compare_and_set(
        self,
        *,
        expected_revision: int,
        snapshot: AdaptiveStateSnapshot,
        outcome_id: str,
    ) -> StateWriteResult:
        raise AssertionError("compare_and_set must not run after failed load")


class _AlwaysConflictStore:
    async def load(self) -> StateLoadResult:
        return StateLoadResult(StateLoadStatus.MISSING)

    async def compare_and_set(
        self,
        *,
        expected_revision: int,
        snapshot: AdaptiveStateSnapshot,
        outcome_id: str,
    ) -> StateWriteResult:
        return StateWriteResult(StateWriteStatus.CONFLICT)


@pytest.mark.asyncio
async def test_conflict_exhaustion_is_an_explicit_retryable_failure() -> None:
    result = await _router(_AlwaysConflictStore()).record_routing_outcome(
        _outcome("conflict-exhausted")
    )

    assert result.status == OutcomeApplicationStatus.CONFLICT_EXHAUSTED
    assert result.learning_updated is False
    assert result.retryable is True


@pytest.mark.asyncio
async def test_store_failure_uses_control_and_never_raises_from_base_routing() -> None:
    router = _router(_FailedStore())
    proposal = await router._get_adaptive_allocation_adjustment(
        _analysis(), CollaborationMode.PARALLEL, None
    )
    result = await router.record_routing_outcome(_outcome("store-error"))

    assert proposal is not None
    assert proposal.proposed_arm == proposal.applied_arm == 0
    assert proposal.control_reason == "state_error"
    assert result.status == OutcomeApplicationStatus.STORE_ERROR
    assert result.learning_updated is False


@pytest.mark.asyncio
async def test_snapshot_restores_posterior_and_mode_baseline() -> None:
    snapshot = _snapshot()
    store = InMemoryAdaptiveStateStore(snapshot)
    router = _router(store)
    status = await router._refresh_adaptive_state()

    assert status == StateLoadStatus.LOADED
    assert router._adaptive_snapshot == snapshot
    assert router._mode_quality_baselines[CollaborationMode.PARALLEL] == 0.81
    assert router._adaptive_engine is not None
    restored = router._adaptive_engine.export_experiment_state()[0]
    assert restored["arm_counts"] == [1, 2, 3, 4, 5]
    assert restored["alpha_params"] == [1.4, 2.1, 3.1, 4.2, 5.5]


@pytest.mark.asyncio
async def test_validated_seed_snapshot_safely_exits_cold_start() -> None:
    router = MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 15,
            "adaptive_routing_min_samples_per_arm": 1,
            "adaptive_routing_performance_threshold": 0.0,
            "adaptive_routing_rng": np.random.default_rng(3),
        },
        adaptive_state_store=InMemoryAdaptiveStateStore(_snapshot()),
    )

    proposal = await router._get_adaptive_allocation_adjustment(
        _analysis(), CollaborationMode.PARALLEL, None
    )

    assert proposal is not None
    assert proposal.ready is True
