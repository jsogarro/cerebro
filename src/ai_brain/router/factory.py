"""Settings-backed construction and lifecycle ownership for MASR.

The active API owns one :class:`MASRRuntime`.  This module deliberately keeps
configuration mapping pure so aliases, dark-launch defaults, and fixture
overrides can be characterized without opening Redis connections.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import redis.asyncio as redis
from structlog import get_logger

from .adaptive_state_store import (
    AdaptiveStateStore,
    InMemoryAdaptiveStateStore,
    RedisAdaptiveStateStore,
    StateLoadStatus,
)
from .masr import MASRouter
from .routing_outcome import (
    EvaluatorEligibilityPolicy,
)

logger = get_logger()


class MASRSettings(Protocol):
    """Settings surface consumed by the MASR factory."""

    REDIS_URL: str
    MASR_DEFAULT_STRATEGY: str
    MASR_ENABLE_CACHING: bool
    MASR_CACHE_MAX_SIZE: int
    MASR_CACHE_EVICTION_BATCH_SIZE: int
    MASR_ENABLE_ADAPTIVE: bool
    MASR_COMPLEXITY_WEIGHTS: dict[str, float]
    MASR_COMPLEXITY_THRESHOLDS: dict[str, float]
    MAX_PARALLEL_WORKERS: int
    AGENTS_MAX_WORKERS_PER_SUPERVISOR: int
    COST_MAX_PER_REQUEST: float
    MEMORY_INFORMED_ROUTING_ENABLED: bool
    MEMORY_ROUTING_MAX_WORKER_ADJUST: int
    MEMORY_ROUTING_FRESHNESS_DAYS: int
    MEMORY_PROMPT_MAX_PROCEDURES: int
    ADAPTIVE_ROUTING_ENABLED: bool
    ADAPTIVE_ROUTING_MIN_HISTORY: int
    ADAPTIVE_ROUTING_MAX_WORKER_ADJUST: int
    ADAPTIVE_ROUTING_UPDATE_INTERVAL_SECONDS: int
    ADAPTIVE_ROUTING_POSTERIOR_TEMP_ENABLED: bool
    ADAPTIVE_ROUTING_POSTERIOR_TEMP_THRESHOLD: int
    ADAPTIVE_ROUTING_POSTERIOR_TEMP_FACTOR: float
    ADAPTIVE_ROUTING_SCHEMA_VERSION: str
    ADAPTIVE_ROUTING_POLICY_VERSION: str
    ADAPTIVE_ROUTING_ALLOWED_EVALUATORS: dict[str, list[str]]


class ClosableRedisClient(Protocol):
    """Redis client methods owned by the runtime."""

    async def get(self, key: str) -> bytes | str | None: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int: ...

    async def aclose(self) -> None: ...


RedisClientFactory = Callable[[str], ClosableRedisClient]


def settings_to_router_config(
    settings: MASRSettings,
    *,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """Map application settings to the router's internal configuration.

    ``MASR_ENABLE_ADAPTIVE`` is the compatibility flag for the older
    history-based strategy heuristic.  It never enables the Thompson bandit;
    only ``ADAPTIVE_ROUTING_ENABLED`` can do that.  Fixture policy is an
    explicit caller-owned override and wins over both memory and bandit flags.
    """

    return {
        "default_strategy": settings.MASR_DEFAULT_STRATEGY,
        "enable_caching": settings.MASR_ENABLE_CACHING,
        "cache": {
            "max_size": settings.MASR_CACHE_MAX_SIZE,
            "eviction_batch_size": settings.MASR_CACHE_EVICTION_BATCH_SIZE,
        },
        "adaptive_strategy_enabled": settings.MASR_ENABLE_ADAPTIVE,
        "fixture_mode": fixture_mode,
        "complexity_analyzer": {
            "weights": dict(settings.MASR_COMPLEXITY_WEIGHTS),
            "thresholds": dict(settings.MASR_COMPLEXITY_THRESHOLDS),
        },
        "max_parallel": settings.MAX_PARALLEL_WORKERS,
        "max_agents": settings.AGENTS_MAX_WORKERS_PER_SUPERVISOR,
        "max_cost": settings.COST_MAX_PER_REQUEST,
        "memory_informed_routing_enabled": (
            False if fixture_mode else settings.MEMORY_INFORMED_ROUTING_ENABLED
        ),
        "memory_routing_max_worker_adjust": settings.MEMORY_ROUTING_MAX_WORKER_ADJUST,
        "memory_routing_freshness_days": settings.MEMORY_ROUTING_FRESHNESS_DAYS,
        "memory_prompt_max_procedures": settings.MEMORY_PROMPT_MAX_PROCEDURES,
        "adaptive_routing_enabled": (
            False if fixture_mode else settings.ADAPTIVE_ROUTING_ENABLED
        ),
        "adaptive_routing_min_history": settings.ADAPTIVE_ROUTING_MIN_HISTORY,
        "adaptive_routing_max_worker_adjust": (
            settings.ADAPTIVE_ROUTING_MAX_WORKER_ADJUST
        ),
        "adaptive_routing_update_interval_seconds": (
            settings.ADAPTIVE_ROUTING_UPDATE_INTERVAL_SECONDS
        ),
        "adaptive_routing_posterior_temp_enabled": (
            settings.ADAPTIVE_ROUTING_POSTERIOR_TEMP_ENABLED
        ),
        "adaptive_routing_posterior_temp_threshold": (
            settings.ADAPTIVE_ROUTING_POSTERIOR_TEMP_THRESHOLD
        ),
        "adaptive_routing_posterior_temp_factor": (
            settings.ADAPTIVE_ROUTING_POSTERIOR_TEMP_FACTOR
        ),
        "adaptive_routing_schema_version": settings.ADAPTIVE_ROUTING_SCHEMA_VERSION,
        "adaptive_routing_policy_version": settings.ADAPTIVE_ROUTING_POLICY_VERSION,
    }


def _allowed_evaluators(
    configured: Mapping[str, Sequence[str]],
) -> Mapping[str, frozenset[str]]:
    return {
        name: frozenset(versions)
        for name, versions in configured.items()
        if name and versions
    }


def create_masr_router(
    settings: MASRSettings,
    *,
    fixture_mode: bool = False,
    adaptive_state_store: AdaptiveStateStore | None = None,
) -> MASRouter:
    """Create a settings-backed router without performing I/O."""

    policy = EvaluatorEligibilityPolicy(
        policy_version=settings.ADAPTIVE_ROUTING_POLICY_VERSION,
        schema_version=settings.ADAPTIVE_ROUTING_SCHEMA_VERSION,
        allowed_evaluators=_allowed_evaluators(
            settings.ADAPTIVE_ROUTING_ALLOWED_EVALUATORS
        ),
    )
    return MASRouter(
        config=settings_to_router_config(settings, fixture_mode=fixture_mode),
        adaptive_state_store=adaptive_state_store,
        outcome_eligibility_policy=policy,
    )


def _default_redis_client_factory(url: str) -> ClosableRedisClient:
    return cast(
        ClosableRedisClient,
        redis.from_url(url, decode_responses=False),
    )


@dataclass
class MASRRuntime:
    """Application-owned MASR router, durable state store, and Redis client."""

    router: MASRouter
    state_store: AdaptiveStateStore
    redis_client: ClosableRedisClient | None = None
    state_status: StateLoadStatus = StateLoadStatus.MISSING
    closed: bool = False

    @classmethod
    async def create(
        cls,
        settings: MASRSettings,
        *,
        fixture_mode: bool = False,
        redis_client_factory: RedisClientFactory = _default_redis_client_factory,
    ) -> MASRRuntime:
        """Build the runtime and restore durable state when Thompson is enabled."""

        thompson_enabled = settings.ADAPTIVE_ROUTING_ENABLED and not fixture_mode
        redis_client: ClosableRedisClient | None = None
        if thompson_enabled:
            redis_client = redis_client_factory(settings.REDIS_URL)
            state_store: AdaptiveStateStore = RedisAdaptiveStateStore(
                redis_client,
                schema_version=settings.ADAPTIVE_ROUTING_SCHEMA_VERSION,
                policy_version=settings.ADAPTIVE_ROUTING_POLICY_VERSION,
            )
        else:
            state_store = InMemoryAdaptiveStateStore()

        try:
            router = create_masr_router(
                settings,
                fixture_mode=fixture_mode,
                adaptive_state_store=state_store,
            )
            state_status = await router.initialize_adaptive_state()
            return cls(
                router=router,
                state_store=state_store,
                redis_client=redis_client,
                state_status=state_status,
            )
        except BaseException:
            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except BaseException as close_exc:
                    logger.warning(
                        "masr_partial_redis_close_failed",
                        error=type(close_exc).__name__,
                    )
            raise

    async def close(self) -> None:
        """Close owned resources exactly once."""

        if self.closed:
            return
        try:
            await self.router.close()
        finally:
            try:
                if self.redis_client is not None:
                    await self.redis_client.aclose()
            finally:
                self.closed = True


__all__ = [
    "MASRRuntime",
    "MASRSettings",
    "RedisClientFactory",
    "create_masr_router",
    "settings_to_router_config",
]
