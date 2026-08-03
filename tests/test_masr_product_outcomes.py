"""Wave 3 product outcome, fixture, status, and convergence contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai_brain.router.adaptive_state_store import (
    AdaptiveExperimentSnapshot,
    AdaptiveStateSnapshot,
    InMemoryAdaptiveStateStore,
    RedisAdaptiveStateStore,
    StateLoadStatus,
)
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.outcome_recorder import (
    ExecutedAllocationOutcome,
    RoutingOutcomeRecorder,
    derive_opaque_identifier,
)
from src.ai_brain.router.query_decomposer import QueryDecomposition
from src.ai_brain.router.routing_observability import (
    masr_adaptive_effective_state,
)
from src.ai_brain.router.routing_outcome import (
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeApplicationResult,
    OutcomeApplicationStatus,
    OutcomeEligibility,
    OutcomeEligibilityReason,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import (
    CollaborationMode,
    RoutingExecutionPolicy,
)
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import RoutingFeedback
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)


@dataclass
class _Allocation:
    supervisor_type: str = "research"
    worker_count: int = 2
    worker_types: list[str] = field(default_factory=lambda: ["research"])


@dataclass
class _Analysis:
    decomposition: QueryDecomposition | None = None


@dataclass
class _Decision:
    query_id: str = "generated-routing-id-is-not-used-for-outcome-id"
    collaboration_mode: CollaborationMode = CollaborationMode.PARALLEL
    agent_allocation: _Allocation = field(default_factory=_Allocation)
    complexity_analysis: _Analysis = field(default_factory=_Analysis)
    adaptive_metadata: Any = None


class _CaptureRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ExecutedAllocationOutcome]] = []

    async def record(
        self,
        decision: Any,
        observation: ExecutedAllocationOutcome,
    ) -> None:
        self.calls.append((decision, observation))


def _project() -> ResearchProject:
    return ResearchProject(
        title="Synthetic fixture research",
        query=ResearchQuery(
            text="Compare synthetic research methods",
            domains=["research", "analytics"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="synthetic-user",
        scope=ResearchScope(max_sources=3),
    )


def test_opaque_identifiers_are_retry_stable_and_non_revealing() -> None:
    first = derive_opaque_identifier("outcome", "run-1", "domain:finance")
    retry = derive_opaque_identifier("outcome", "run-1", "domain:finance")
    other = derive_opaque_identifier("outcome", "run-1", "domain:research")

    assert first == retry
    assert first != other
    assert first.startswith("out_")
    assert "finance" not in first
    assert len(first) == 68


@pytest.mark.asyncio
async def test_recorder_retries_with_same_outcome_id_and_keeps_cost_unavailable() -> (
    None
):
    captured: list[RoutingOutcome] = []

    class _RetryingRouter:
        adaptive_policy_version = "masr-adaptive-v1"
        adaptive_schema_version = "1"

        async def record_routing_outcome(
            self,
            outcome: RoutingOutcome,
        ) -> OutcomeApplicationResult:
            captured.append(outcome)
            if len(captured) == 1:
                return OutcomeApplicationResult(
                    status=OutcomeApplicationStatus.STORE_ERROR,
                    outcome=outcome,
                    learning_updated=False,
                    retryable=True,
                    reason="synthetic_store_error",
                )
            evaluated = outcome.with_eligibility(
                OutcomeEligibility(
                    eligible=False,
                    reason=OutcomeEligibilityReason.SOURCE_NOT_EVALUATOR,
                )
            )
            return OutcomeApplicationResult(
                status=OutcomeApplicationStatus.INELIGIBLE_RECORDED,
                outcome=evaluated,
                learning_updated=False,
                reason="source_not_evaluator",
            )

    recorder = RoutingOutcomeRecorder(
        _RetryingRouter(),  # type: ignore[arg-type]
        retry_delay_seconds=0,
    )
    result = await recorder.record(
        _Decision(),
        ExecutedAllocationOutcome(
            execution_id="run-1",
            allocation_key="primary",
            allocation_attempt_id="rt_opaque-allocation-attempt",
            execution_status="completed",
            latency_ms=12,
            source=OutcomeSource.HEURISTIC,
        ),
    )

    assert result.learning_updated is False
    assert len(captured) == 2
    assert captured[0].outcome_id == captured[1].outcome_id
    assert captured[0].routing_id == captured[1].routing_id
    assert captured[0].measured_cost is None
    assert captured[0].cost_availability is MetricAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_explicit_fixture_policy_is_deterministic_provider_free_and_store_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "ADAPTIVE_ROUTING_ENABLED",
        "MEMORY_INFORMED_ROUTING_ENABLED",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.setenv(name, "true" if name.endswith("ENABLED") else "")

    store = Mock()
    store.load = AsyncMock(side_effect=AssertionError("fixture touched adaptive store"))
    store.compare_and_set = AsyncMock(
        side_effect=AssertionError("fixture touched adaptive store")
    )
    router = MASRouter(
        config={
            "enable_caching": True,
            "adaptive_routing_enabled": True,
            "memory_informed_routing_enabled": True,
        },
        adaptive_state_store=store,
    )
    memory = Mock()
    memory.retrieve_episodes = AsyncMock(
        side_effect=AssertionError("fixture touched episodic memory")
    )
    router.episodic_memory = memory
    bridge = Mock()
    bridge.execute_routing_decision = AsyncMock(
        side_effect=AssertionError("fixture invoked a supervisor/provider")
    )
    provider = Mock()
    provider.generate_content = AsyncMock(
        side_effect=AssertionError("fixture invoked a provider")
    )
    recorder = _CaptureRecorder()
    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        gemini_service=provider,
        outcome_recorder=recorder,  # type: ignore[arg-type]
    )
    fixture_payload = {
        "artifact": "synthetic source-grounded fixture",
        "claims": [{"id": "claim-1", "evidence": ["evidence-1"]}],
    }
    outputs: list[dict[str, Any] | None] = []
    decisions: list[dict[str, Any] | None] = []
    project = _project()
    for execution_id in ("fixture-run-1", "fixture-run-2"):
        status = ExecutionStatus(
            execution_id=execution_id,
            project_id=str(project.id),
            status="pending",
        )
        # routing_decision/execution_plan are now required parameters (the
        # authority-bound compiled plan, not something computed internally
        # by _execute_research_workflow); fixture mode is checked after the
        # authority guard, so a minimal stand-in decision satisfies it
        # without touching MASR/the adaptive store.
        await service._execute_research_workflow(
            project,
            status,
            execution_policy=RoutingExecutionPolicy.fixture(),
            fixture_result=fixture_payload,
            routing_decision=_Decision(
                adaptive_metadata={"status": "fixture_off", "enabled": False}
            ),
            execution_plan=Mock(),
        )
        outputs.append(status.final_output)
        decisions.append(status.routing_decision)

    assert outputs == [fixture_payload, fixture_payload]
    assert all(
        decision
        and decision["adaptive_metadata"]["status"] == "fixture_off"
        and decision["adaptive_metadata"]["enabled"] is False
        for decision in decisions
    )
    assert store.load.await_count == 0
    assert store.compare_and_set.await_count == 0
    assert memory.retrieve_episodes.await_count == 0
    assert bridge.execute_routing_decision.await_count == 0
    assert provider.generate_content.await_count == 0
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_status_distinguishes_disabled_fixture_cold_degraded_and_active() -> None:
    disabled = MASRouter(config={"adaptive_routing_enabled": False})
    fixture = MASRouter(config={"adaptive_routing_enabled": True, "fixture_mode": True})
    cold = MASRouter(
        config={"adaptive_routing_enabled": True},
        adaptive_state_store=InMemoryAdaptiveStateStore(),
    )
    degraded = MASRouter(config={"adaptive_routing_enabled": True})
    degraded._adaptive_store_healthy = False
    degraded._adaptive_state_status = StateLoadStatus.ERROR
    experiment = AdaptiveExperimentSnapshot(
        experiment_id="adaptive_allocation_parallel",
        ordered_arms=(-2, -1, 0, 1, 2),
        arm_counts=(1, 1, 1, 1, 1),
        arm_reward_sums=(0.5, 0.5, 0.5, 0.5, 0.5),
        arm_values=(0.5, 0.5, 0.5, 0.5, 0.5),
        alpha_params=(1.5, 1.5, 1.5, 1.5, 1.5),
        beta_params=(1.5, 1.5, 1.5, 1.5, 1.5),
    )
    snapshot = AdaptiveStateSnapshot(
        revision=1,
        experiments=(experiment,),
        eligible_outcome_count=5,
        processed_outcome_count=5,
    )
    active = MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 5,
            "adaptive_routing_min_samples_per_arm": 1,
        },
        adaptive_state_store=InMemoryAdaptiveStateStore(snapshot),
    )
    await active.initialize_adaptive_state()

    states = [
        (await disabled.get_adaptive_status())["effective_state"],
        (await fixture.get_adaptive_status())["effective_state"],
        (await cold.get_adaptive_status())["effective_state"],
        (await degraded.get_adaptive_status())["effective_state"],
        (await active.get_adaptive_status())["effective_state"],
    ]

    assert states == ["disabled", "fixture_off", "cold", "degraded", "active"]
    active_status = await active.get_adaptive_status()
    assert active_status["per_arm_counts"] == {
        "-2": 1,
        "-1": 1,
        "0": 1,
        "1": 1,
        "2": 1,
    }
    assert active_status["schema_version"] == "1"
    assert active_status["store_health"] == "healthy"


@pytest.mark.asyncio
async def test_global_status_requires_every_observed_experiment_to_be_ready() -> None:
    ready_experiment = AdaptiveExperimentSnapshot(
        experiment_id="adaptive_allocation_parallel",
        ordered_arms=(-2, -1, 0, 1, 2),
        arm_counts=(1, 1, 1, 1, 1),
        arm_reward_sums=(0.5, 0.5, 0.5, 0.5, 0.5),
        arm_values=(0.5, 0.5, 0.5, 0.5, 0.5),
        alpha_params=(1.5, 1.5, 1.5, 1.5, 1.5),
        beta_params=(1.5, 1.5, 1.5, 1.5, 1.5),
    )
    cold_experiment = AdaptiveExperimentSnapshot(
        experiment_id="adaptive_allocation_direct",
        ordered_arms=(-2, -1, 0, 1, 2),
        arm_counts=(0, 0, 0, 0, 0),
        arm_reward_sums=(0.0, 0.0, 0.0, 0.0, 0.0),
        arm_values=(0.0, 0.0, 0.0, 0.0, 0.0),
        alpha_params=(1.0, 1.0, 1.0, 1.0, 1.0),
        beta_params=(1.0, 1.0, 1.0, 1.0, 1.0),
    )
    snapshot = AdaptiveStateSnapshot(
        revision=1,
        experiments=(ready_experiment, cold_experiment),
        eligible_outcome_count=5,
        processed_outcome_count=5,
    )
    router = MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 5,
            "adaptive_routing_min_samples_per_arm": 1,
        },
        adaptive_state_store=InMemoryAdaptiveStateStore(snapshot),
    )

    await router.initialize_adaptive_state()
    status = await router.get_adaptive_status()

    assert status["effective_state"] == "cold"
    assert status["ready"] is False
    assert status["experiment_readiness"] == {
        "adaptive_allocation_direct": False,
        "adaptive_allocation_parallel": True,
    }


@pytest.mark.asyncio
async def test_effective_state_gauge_updates_without_status_endpoint_access() -> None:
    router = MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 1,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_performance_threshold": 0.0,
        },
        adaptive_state_store=InMemoryAdaptiveStateStore(),
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"quality-gate": frozenset({"1"})}
        ),
    )

    assert masr_adaptive_effective_state.labels(state="cold")._value.get() == 1

    result = await router.record_routing_outcome(
        RoutingOutcome(
            outcome_id="gauge-outcome",
            routing_id="gauge-routing",
            policy_version="masr-adaptive-v1",
            source=OutcomeSource.EVALUATOR,
            collaboration_mode=CollaborationMode.PARALLEL,
            proposed_arm=0,
            applied_arm=0,
            final_worker_count=2,
            execution_status="completed",
            latency_ms=5,
            measured_cost=None,
            cost_availability=MetricAvailability.UNAVAILABLE,
            quality_score=0.8,
            quality_availability=MetricAvailability.MEASURED,
            evaluator_name="quality-gate",
            evaluator_version="1",
        )
    )

    assert result.status is OutcomeApplicationStatus.APPLIED
    assert masr_adaptive_effective_state.labels(state="active")._value.get() == 1
    assert masr_adaptive_effective_state.labels(state="cold")._value.get() == 0

    router._adaptive_state_store = _FailedLoadStore()
    await router.initialize_adaptive_state()

    assert masr_adaptive_effective_state.labels(state="degraded")._value.get() == 1
    assert masr_adaptive_effective_state.labels(state="active")._value.get() == 0


class _FailedLoadStore:
    async def load(self) -> Any:
        from src.ai_brain.router.adaptive_state_store import StateLoadResult

        return StateLoadResult(StateLoadStatus.ERROR, reason="synthetic failure")

    async def compare_and_set(self, **kwargs: Any) -> Any:
        raise AssertionError("compare_and_set must not run after failed load")


@pytest.mark.asyncio
async def test_manual_feedback_is_truthfully_ineligible() -> None:
    service = MASRRoutingService(router=MASRouter())
    service.routing_history["route-1"] = _Decision()  # type: ignore[assignment]

    response = await service.submit_feedback(
        RoutingFeedback(
            routing_id="route-1",
            actual_cost=0.1,
            actual_latency_ms=10,
            quality_score=0.9,
        )
    )

    assert response == {
        "status": "accepted",
        "routing_id": "route-1",
        "feedback_processed": True,
        "learning_updated": False,
        "recorded": False,
        "eligible": False,
        "duplicate": False,
        "source": "manual",
        "reason": "manual_feedback_has_no_executed_allocation_or_evaluator_proof",
    }


class _AtomicRedis:
    """Injected Redis double implementing the store's atomic Lua contract."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> bytes | None:
        async with self._lock:
            return self.values.get(key)

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        del script
        assert numkeys == 2
        state_key, marker_key, expected, payload, retention = args
        assert int(retention) > 0
        async with self._lock:
            if marker_key in self.values:
                return 2
            current_payload = self.values.get(state_key)
            current_revision = (
                int(json.loads(current_payload)["revision"])
                if current_payload is not None
                else 0
            )
            if current_revision != int(expected):
                return 0
            self.values[state_key] = payload.encode()
            self.values[marker_key] = b"1"
            return 1


def _eligible_outcome(outcome_id: str) -> RoutingOutcome:
    return RoutingOutcome(
        outcome_id=outcome_id,
        routing_id=f"routing-{outcome_id}",
        policy_version="masr-adaptive-v1",
        source=OutcomeSource.EVALUATOR,
        collaboration_mode=CollaborationMode.PARALLEL,
        proposed_arm=0,
        applied_arm=0,
        final_worker_count=2,
        execution_status="completed",
        latency_ms=5,
        measured_cost=None,
        cost_availability=MetricAvailability.UNAVAILABLE,
        quality_score=0.8,
        quality_availability=MetricAvailability.MEASURED,
        evaluator_name="quality-gate",
        evaluator_version="1",
    )


def _redis_router(client: _AtomicRedis) -> MASRouter:
    return MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 0,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_conflict_retries": 3,
            "adaptive_routing_conflict_backoff_seconds": 0,
        },
        adaptive_state_store=RedisAdaptiveStateStore(client),
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"quality-gate": frozenset({"1"})}
        ),
    )


@pytest.mark.asyncio
async def test_redis_restart_and_two_instance_unique_duplicate_convergence() -> None:
    client = _AtomicRedis()
    first = _redis_router(client)
    second = _redis_router(client)

    unique_results = await asyncio.gather(
        first.record_routing_outcome(_eligible_outcome("unique-1")),
        second.record_routing_outcome(_eligible_outcome("unique-2")),
    )
    duplicate_results = await asyncio.gather(
        first.record_routing_outcome(_eligible_outcome("duplicate")),
        second.record_routing_outcome(_eligible_outcome("duplicate")),
    )
    restarted = _redis_router(client)
    load_status = await restarted.initialize_adaptive_state()
    status = await restarted.get_adaptive_status()

    assert {result.status for result in unique_results} == {
        OutcomeApplicationStatus.APPLIED
    }
    assert {result.status for result in duplicate_results} == {
        OutcomeApplicationStatus.APPLIED,
        OutcomeApplicationStatus.DUPLICATE,
    }
    assert load_status is StateLoadStatus.LOADED
    assert status["eligible_outcome_count"] == 3
    assert status["processed_outcome_count"] == 3
