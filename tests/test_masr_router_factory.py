"""Configuration and lifecycle tests for the canonical MASR runtime."""

from __future__ import annotations

from typing import Any

import pytest

from src.ai_brain.router.adaptive_state_store import (
    AdaptiveStateSnapshot,
    RedisAdaptiveStateStore,
    StateLoadStatus,
)
from src.ai_brain.router.factory import MASRRuntime, settings_to_router_config
from src.ai_brain.router.routing_outcome import (
    MetricAvailability,
    OutcomeApplicationStatus,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import CollaborationMode
from src.core.config import Settings


class FakeRedis:
    def __init__(self, *, fail_load: bool = False) -> None:
        self.fail_load = fail_load
        self.closed = False
        self.get_calls = 0
        self.payload: str | bytes | None = None
        self.eval_calls: list[tuple[str, ...]] = []

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        if self.fail_load:
            raise ConnectionError
        return self.payload

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        self.eval_calls.append(args)
        self.payload = args[3]
        return 1

    async def aclose(self) -> None:
        self.closed = True

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


def _settings(**updates: Any) -> Settings:
    base = Settings(_env_file=None)
    return base.model_copy(update=updates)


def test_mapper_keeps_old_heuristic_and_thompson_flags_separate() -> None:
    config = settings_to_router_config(
        _settings(
            MASR_ENABLE_ADAPTIVE=True,
            ADAPTIVE_ROUTING_ENABLED=False,
        )
    )

    assert config["adaptive_strategy_enabled"] is True
    assert config["adaptive_routing_enabled"] is False
    assert "enable_adaptive" not in config


def test_mapper_carries_cache_memory_budget_and_version_settings() -> None:
    config = settings_to_router_config(
        _settings(
            MASR_CACHE_MAX_SIZE=123,
            MASR_CACHE_EVICTION_BATCH_SIZE=7,
            MEMORY_INFORMED_ROUTING_ENABLED=True,
            COST_MAX_PER_REQUEST=0.25,
            ADAPTIVE_ROUTING_SCHEMA_VERSION="1",
            ADAPTIVE_ROUTING_POLICY_VERSION="masr-adaptive-v1",
        )
    )

    assert config["cache"] == {"max_size": 123, "eviction_batch_size": 7}
    assert config["memory_informed_routing_enabled"] is True
    assert config["max_cost"] == 0.25
    assert config["adaptive_routing_schema_version"] == "1"
    assert config["adaptive_routing_policy_version"] == "masr-adaptive-v1"


def test_explicit_fixture_policy_disables_memory_and_thompson() -> None:
    config = settings_to_router_config(
        _settings(
            MEMORY_INFORMED_ROUTING_ENABLED=True,
            ADAPTIVE_ROUTING_ENABLED=True,
        ),
        fixture_mode=True,
    )

    assert config["memory_informed_routing_enabled"] is False
    assert config["adaptive_routing_enabled"] is False


@pytest.mark.asyncio
async def test_disabled_runtime_never_constructs_or_reads_redis() -> None:
    factory_calls = 0

    def forbidden_factory(url: str) -> FakeRedis:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("disabled runtime must not construct Redis")

    runtime = await MASRRuntime.create(
        _settings(ADAPTIVE_ROUTING_ENABLED=False),
        redis_client_factory=forbidden_factory,
    )

    assert factory_calls == 0
    assert runtime.router.adaptive_routing_enabled is False
    assert runtime.state_status is StateLoadStatus.MISSING
    await runtime.close()
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_enabled_runtime_restores_from_its_owned_redis_store() -> None:
    client = FakeRedis()
    runtime = await MASRRuntime.create(
        _settings(ADAPTIVE_ROUTING_ENABLED=True),
        redis_client_factory=lambda _: client,
    )

    assert isinstance(runtime.state_store, RedisAdaptiveStateStore)
    assert runtime.router._adaptive_state_store is runtime.state_store
    assert runtime.state_status is StateLoadStatus.MISSING
    assert client.get_calls == 1

    await runtime.close()
    await runtime.close()
    assert client.closed is True


@pytest.mark.asyncio
async def test_runtime_create_closes_redis_when_router_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.ai_brain.router import factory as factory_module

    client = FakeRedis()

    def fail_router_construction(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("router construction failed")

    monkeypatch.setattr(
        factory_module,
        "create_masr_router",
        fail_router_construction,
    )

    with pytest.raises(RuntimeError, match="router construction failed"):
        await MASRRuntime.create(
            _settings(ADAPTIVE_ROUTING_ENABLED=True),
            redis_client_factory=lambda _: client,
        )

    assert client.closed is True


@pytest.mark.asyncio
async def test_redis_restore_failure_degrades_adaptation_without_blocking_runtime() -> (
    None
):
    client = FakeRedis(fail_load=True)
    runtime = await MASRRuntime.create(
        _settings(ADAPTIVE_ROUTING_ENABLED=True),
        redis_client_factory=lambda _: client,
    )

    assert runtime.state_status is StateLoadStatus.ERROR
    assert runtime.router._adaptive_store_healthy is False
    assert runtime.router.adaptive_routing_enabled is True
    await runtime.close()


@pytest.mark.asyncio
async def test_non_default_versions_flow_through_runtime_state_and_metadata() -> None:
    client = FakeRedis()
    runtime = await MASRRuntime.create(
        _settings(
            ADAPTIVE_ROUTING_ENABLED=True,
            ADAPTIVE_ROUTING_SCHEMA_VERSION="schema-2",
            ADAPTIVE_ROUTING_POLICY_VERSION="policy-2",
            ADAPTIVE_ROUTING_ALLOWED_EVALUATORS={"quality-gate": ["eval-2"]},
            ADAPTIVE_ROUTING_MIN_HISTORY=0,
        ),
        redis_client_factory=lambda _: client,
    )

    assert runtime.router._adaptive_snapshot.schema_version == "schema-2"
    assert runtime.router._adaptive_snapshot.policy_version == "policy-2"

    result = await runtime.router.record_routing_outcome(
        RoutingOutcome(
            outcome_id="versioned-outcome",
            routing_id="versioned-routing",
            schema_version="schema-2",
            policy_version="policy-2",
            source=OutcomeSource.EVALUATOR,
            collaboration_mode=CollaborationMode.PARALLEL,
            proposed_arm=1,
            applied_arm=1,
            final_worker_count=4,
            execution_status="completed",
            latency_ms=120,
            measured_cost=None,
            cost_availability=MetricAvailability.UNAVAILABLE,
            quality_score=0.9,
            quality_availability=MetricAvailability.MEASURED,
            evaluator_name="quality-gate",
            evaluator_version="eval-2",
        )
    )
    loaded = await runtime.state_store.load()
    decision = await runtime.router.route(
        "Compare two source-grounded research methods"
    )

    assert result.status is OutcomeApplicationStatus.APPLIED
    assert loaded.snapshot is not None
    assert isinstance(loaded.snapshot, AdaptiveStateSnapshot)
    assert loaded.snapshot.schema_version == "schema-2"
    assert loaded.snapshot.policy_version == "policy-2"
    assert client.eval_calls
    assert decision.adaptive_metadata is not None
    assert decision.adaptive_metadata.schema_version == "schema-2"
    assert decision.adaptive_metadata.policy_version == "policy-2"
    await runtime.close()


@pytest.mark.asyncio
async def test_legacy_service_uses_shared_mapper_but_forces_thompson_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.ai_brain.router import masr_service as legacy_module

    client = FakeRedis()
    service = legacy_module.MASRService()
    service.settings = _settings(
        MASR_DEFAULT_STRATEGY="quality_focused",
        MASR_ENABLE_ADAPTIVE=False,
        ADAPTIVE_ROUTING_ENABLED=True,
    )
    monkeypatch.setattr(legacy_module.redis, "from_url", lambda _: client)

    await service._initialize_components()

    assert service.masr_router is not None
    assert service.masr_router.default_strategy.value == "quality_focused"
    assert service.masr_router.adaptive_strategy_enabled is False
    assert service.masr_router.adaptive_routing_enabled is False
    await service._cleanup_components()
    assert client.closed is True
