"""
MASR (Multi-Agent System Router) - Core Intelligence Engine

The Multi-Agent System Router is the central intelligence component that:
1. Analyzes incoming queries for complexity and requirements
2. Optimizes model selection for cost and performance
3. Determines optimal agent allocation and coordination strategy
4. Routes requests to appropriate supervisors and workers
5. Manages fallback strategies and error recovery
6. Tracks performance metrics for continuous improvement

This is the brain of Cerebro's AI orchestration system, making intelligent
decisions about how to handle each query most effectively.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
from structlog import get_logger

from src.core.pii_redactor import redact_pii
from src.core.tracing import trace_masr_routing
from src.core.types import HealthCheckDict

if TYPE_CHECKING:
    from src.ai_brain.config.model_config_manager import ModelConfigManager
    from src.ai_brain.memory.episodic_memory import EpisodicMemoryManager
    from src.ai_brain.memory.procedural_memory import ProceduralMemoryManager

from src.ai_brain.experimentation.core.adaptive_allocation_engine import (
    AdaptiveAllocationEngine,
    AllocationConfig,
    AllocationStrategy,
)
from src.core.constants import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_ESTIMATED_TOKENS,
    DEFAULT_RETRY_ATTEMPTS,
    DIRECT_MODE_PARALLELISM,
    HIGH_PARALLELISM,
    LONG_TIMEOUT,
    LOW_PARALLELISM,
    MAX_RETRY_ATTEMPTS,
    MEDIUM_TIMEOUT,
    MIN_RETRY_ATTEMPTS,
    SHORT_TIMEOUT,
)
from src.reliability.retry_strategies import CircuitBreaker, CircuitBreakerConfig

from .adaptive_state_store import (
    AdaptiveExperimentSnapshot,
    AdaptiveStateSnapshot,
    AdaptiveStateStore,
    InMemoryAdaptiveStateStore,
    StateLoadStatus,
    StateWriteStatus,
    empty_adaptive_snapshot,
)
from .cost_optimizer import CostOptimizer, OptimizationResult, OptimizationStrategy
from .query_analyzer import ComplexityAnalysis, ComplexityLevel, QueryComplexityAnalyzer
from .routing_cache import RoutingCacheManager
from .routing_metrics import RoutingMetricsCollector
from .routing_observability import observe_effective_state
from .routing_outcome import (
    ADAPTIVE_ARMS,
    ADAPTIVE_OUTCOME_SCHEMA_VERSION,
    ADAPTIVE_POLICY_VERSION,
    EvaluatorEligibilityPolicy,
    OutcomeApplicationResult,
    OutcomeApplicationStatus,
    RoutingOutcome,
)
from .routing_types import (
    AdaptiveAllocationProposal,
    AdaptiveDecisionMetadata,
    AdaptiveRoutingStatus,
    AgentAllocation,
    CollaborationMode,
    RoutingExecutionPolicy,
    RoutingMetrics,
    RoutingStrategy,
)

logger = get_logger()


@dataclass
class RoutingDecision:
    """Complete routing decision with all specifications."""

    # Query identification
    query_id: str
    timestamp: datetime

    # Analysis results
    complexity_analysis: ComplexityAnalysis
    optimization_result: OptimizationResult

    # Routing specifications
    collaboration_mode: CollaborationMode
    agent_allocation: AgentAllocation

    # Performance predictions
    estimated_cost: float
    estimated_latency_ms: int
    estimated_quality: float
    confidence_score: float

    # High-level routing strategy that produced this decision. Preserved
    # explicitly because the forward map to ``OptimizationStrategy`` is lossy
    # (both BALANCED and ADAPTIVE map to OptimizationStrategy.BALANCED), so it
    # cannot be recovered from ``optimization_result.strategy_used``.
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED

    # Execution details
    fallback_strategy: str = "graceful_degradation"
    monitoring_level: str = "standard"  # minimal, standard, detailed

    # Context preservation
    context_requirements: dict[str, Any] = field(default_factory=dict)
    memory_allocation: dict[str, int] = field(default_factory=dict)
    adaptive_metadata: AdaptiveDecisionMetadata | None = None


class MASRouter:
    """
    Multi-Agent System Router - The central intelligence of Cerebro.

    Combines complexity analysis with cost optimization to make intelligent
    routing decisions that balance performance, cost, and quality based on
    query requirements and system constraints.

    Now supports dynamic model configuration for flexible routing.
    """

    # Fast-path uncertainty ceiling. The query analyzer uses INVERTED
    # uncertainty semantics for simple queries: an unclassifiable (GENERAL)
    # single-domain query is floored at exactly 0.30, while confidently
    # classified complex queries score lower. Trivial/chit-chat queries are
    # legitimately GENERAL, so the fast path INCLUDES them by accepting
    # uncertainty at or below this ceiling. This is a deliberately tight
    # coupling to the analyzer's GENERAL penalty (query_analyzer.py); if that
    # penalty changes, revisit this constant and the fast-path tests.
    _FAST_PATH_UNCERTAINTY_CEILING = 0.3

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        model_config_manager: ModelConfigManager | None = None,
        adaptive_state_store: AdaptiveStateStore | None = None,
        outcome_eligibility_policy: EvaluatorEligibilityPolicy | None = None,
    ):
        """Initialize MASR with configuration."""
        self.config = config or {}
        self.model_config_manager = model_config_manager

        # Initialize components
        self.complexity_analyzer = QueryComplexityAnalyzer(
            self.config.get("complexity_analyzer", {})
        )
        cost_opt_config = config.get("cost_optimizer", {}) if config else {}
        self.cost_optimizer = CostOptimizer(cost_opt_config, model_config_manager)

        # Initialize cache manager
        cache_config = self.config.get("cache", {})
        self.cache_manager = RoutingCacheManager(
            enabled=self.config.get("enable_caching", True),
            max_size=cache_config.get("max_size", 1000),
            eviction_batch_size=cache_config.get("eviction_batch_size", 100),
        )

        # Initialize metrics collector
        self.default_strategy = RoutingStrategy(
            self.config.get("default_strategy", "balanced")
        )
        self.metrics_collector = RoutingMetricsCollector(
            default_strategy=self.default_strategy,
            adaptation_window_hours=self.config.get("adaptation_window_hours", 24),
            min_history_for_adaptation=self.config.get(
                "min_history_for_adaptation", 100
            ),
        )

        # Older history-based strategy adaptation. The compatibility
        # ``enable_adaptive`` key is deliberately separate from the Thompson
        # bandit flag below.
        self.adaptive_strategy_enabled = self.config.get(
            "adaptive_strategy_enabled",
            self.config.get("enable_adaptive", True),
        )

        # Performance thresholds
        self.quality_threshold = self.config.get("min_quality", 0.8)
        self.cost_threshold = self.config.get("max_cost", 0.05)
        self.latency_threshold_ms = self.config.get("max_latency_ms", 5000)

        # Agent allocation limits
        self.max_agents_per_query = self.config.get("max_agents", 10)
        self.max_parallel_workers = self.config.get("max_parallel", 5)

        # Store background task references to prevent GC
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Learning parameters for adaptive routing
        self.learning_enabled = self.config.get("enable_learning", True)
        self._routing_circuit_breaker = CircuitBreaker(
            "masr_router",
            CircuitBreakerConfig(
                failure_threshold=self.config.get("circuit_failure_threshold", 5),
                success_threshold=self.config.get("circuit_success_threshold", 2),
                timeout=self.config.get("circuit_timeout_seconds", 60.0),
            ),
        )

        # Memory-informed routing (feature-flagged, default OFF)
        self.memory_informed_routing_enabled = self.config.get(
            "memory_informed_routing_enabled", False
        )
        self.memory_routing_max_worker_adjust = self.config.get(
            "memory_routing_max_worker_adjust", 2
        )
        self.memory_routing_freshness_days = self.config.get(
            "memory_routing_freshness_days", 30
        )
        self.memory_prompt_max_procedures = self.config.get(
            "memory_prompt_max_procedures", 3
        )
        # Memory managers injected externally (None = no memory influence)
        self.episodic_memory: EpisodicMemoryManager | None = None
        self.procedural_memory: ProceduralMemoryManager | None = None

        # Adaptive routing (feature-flagged, default OFF)
        self.adaptive_routing_enabled = self.config.get(
            "adaptive_routing_enabled", False
        )
        self.fixture_mode = bool(self.config.get("fixture_mode", False))
        # Min history raised to 300 (from 100) based on sample-complexity analysis:
        # For 3 modes x 5 arms = 15 contexts with delta_mu=0.15, sigma=0.02, Hoeffding bound
        # requires ~14 samples per arm for 95% confidence -> 450 total minimum.
        # 300 provides reasonable cold-start horizon while staying below full bound.
        self.adaptive_routing_min_history = self.config.get(
            "adaptive_routing_min_history", 300
        )
        self.adaptive_routing_max_worker_adjust = self.config.get(
            "adaptive_routing_max_worker_adjust", 2
        )
        self.adaptive_schema_version = str(
            self.config.get(
                "adaptive_routing_schema_version",
                ADAPTIVE_OUTCOME_SCHEMA_VERSION,
            )
        )
        self.adaptive_policy_version = str(
            self.config.get(
                "adaptive_routing_policy_version",
                ADAPTIVE_POLICY_VERSION,
            )
        )
        self._adaptive_engine: AdaptiveAllocationEngine | None = None
        # Per-mode quality baselines for advantage reward computation (EMA)
        self._mode_quality_baselines: dict[CollaborationMode, float] = {}
        self._adaptive_rng = self.config.get("adaptive_routing_rng", np.random)
        self._adaptive_state_store = (
            adaptive_state_store or InMemoryAdaptiveStateStore()
        )
        self._adaptive_snapshot = empty_adaptive_snapshot(
            schema_version=self.adaptive_schema_version,
            policy_version=self.adaptive_policy_version,
        )
        self._adaptive_store_healthy = True
        self._adaptive_state_status = StateLoadStatus.MISSING
        self._adaptive_state_lock = asyncio.Lock()
        self._adaptive_conflict_retries = max(
            0, int(self.config.get("adaptive_routing_conflict_retries", 2))
        )
        self._adaptive_conflict_backoff_seconds = max(
            0.0,
            float(self.config.get("adaptive_routing_conflict_backoff_seconds", 0.01)),
        )
        self._outcome_eligibility_policy = (
            outcome_eligibility_policy
            or EvaluatorEligibilityPolicy(
                schema_version=self.adaptive_schema_version,
                policy_version=self.adaptive_policy_version,
            )
        )
        if (
            self._outcome_eligibility_policy.schema_version
            != self.adaptive_schema_version
            or self._outcome_eligibility_policy.policy_version
            != self.adaptive_policy_version
        ):
            raise ValueError("adaptive router and evaluator policy versions must match")
        if self.adaptive_routing_enabled:
            self._adaptive_engine = AdaptiveAllocationEngine(
                self._adaptive_engine_config()
            )
        self._observe_adaptive_effective_state()

    @property
    def routing_circuit_breaker(self) -> CircuitBreaker:
        """Circuit breaker guarding MASR routing analysis and optimization."""
        return self._routing_circuit_breaker

    async def route(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        strategy: RoutingStrategy | None = None,
        constraints: dict[str, Any] | None = None,
        execution_policy: RoutingExecutionPolicy | None = None,
    ) -> RoutingDecision:
        """
        Route a query through intelligent analysis and optimization.

        Args:
            query: The input query to route
            context: Additional context (user info, session, etc.)
            strategy: Routing strategy override
            constraints: Custom constraints for this request

        Returns:
            RoutingDecision with complete routing specifications
        """
        start_time = datetime.now()
        query_id = str(uuid.uuid4())
        effective_policy = execution_policy or (
            RoutingExecutionPolicy.fixture()
            if self.fixture_mode
            else RoutingExecutionPolicy()
        )

        logger.info("Routing query %s: %s...", query_id, redact_pii(query)[:100])

        # Langfuse v4 sits on OTel, whose set_attributes REPLACES by key rather
        # than merging. Keep the initial request fields in a local and re-send
        # them on every trace.update() so they are not dropped from the trace.
        initial_trace_metadata = {
            "strategy_override": (
                strategy.value if isinstance(strategy, RoutingStrategy) else strategy
            ),
            "has_constraints": constraints is not None,
        }

        with trace_masr_routing(
            query_id=query_id,
            query=query,
            metadata=initial_trace_metadata,
        ) as trace:
            if context is None:
                context = {}
            # Do NOT mutate the caller's context dict — the trace handle is a
            # per-request, non-serializable object, and writing it into the
            # shared context leaks it back into the caller and into the cached
            # decision's stored context (a confirmed cross-request hazard). The
            # trace handle is already available as the local ``trace`` below and
            # is threaded to the provider via request.metadata at the call site,
            # so no context mutation is needed here.

            try:
                await self._routing_circuit_breaker.call(lambda: None)

                # Check cache first if enabled. The strategy and constraints are
                # part of the cache identity — the same query routes differently
                # under a different strategy or cost/quality constraints.
                cached_decision = (
                    None
                    if self.adaptive_routing_enabled or effective_policy.fixture_mode
                    else self.cache_manager.check_cache(
                        query, context, strategy, constraints
                    )
                )
                if cached_decision:
                    logger.info("routing_cache_hit", query_id=query_id)
                    await self._routing_circuit_breaker._on_success()
                    if trace is not None:
                        try:
                            trace.update(
                                metadata={
                                    **initial_trace_metadata,
                                    "cache_hit": True,
                                    "collaboration_mode": cached_decision.collaboration_mode.value,
                                    "worker_count": cached_decision.agent_allocation.worker_count,
                                }
                            )
                        except Exception as trace_err:
                            logger.debug(
                                "trace_update_failed",
                                error=str(trace_err),
                                query_id=query_id,
                            )
                    return cached_decision

                # Step 1: Analyze query complexity
                complexity_analysis = await self.complexity_analyzer.analyze(
                    query, context
                )

                # Step 2: Optimize model selection.
                # ``strategy`` may arrive as a bare string when the caller used a
                # Pydantic model with ``use_enum_values=True`` (e.g. RoutingRequest),
                # so normalize to a RoutingStrategy member before it is stored on the
                # decision and used for ``.value`` lookups downstream.
                if strategy is not None:
                    routing_strategy = RoutingStrategy(strategy)
                else:
                    routing_strategy = self._select_routing_strategy(
                        complexity_analysis, context
                    )
                optimization_strategy = self._map_to_optimization_strategy(
                    routing_strategy
                )

                optimization_result = await self.cost_optimizer.optimize(
                    complexity_analysis, optimization_strategy, constraints
                )

                # Step 3: Determine collaboration mode
                collaboration_mode = self._determine_collaboration_mode(
                    complexity_analysis, optimization_result
                )

                # Step 3.5: Query episodic memory for routing prior (if enabled)
                episodic_prior = (
                    await self._get_episodic_routing_prior(complexity_analysis, query)
                    if effective_policy.memory_routing_allowed
                    else None
                )

                # Step 3.6: Query adaptive engine for allocation adjustment (if enabled)
                adaptive_recommendation = (
                    await self._get_adaptive_allocation_adjustment(
                        complexity_analysis, collaboration_mode, episodic_prior
                    )
                    if effective_policy.adaptive_routing_allowed
                    else None
                )

                # Step 4: Allocate agents (with optional memory + adaptive adjustment)
                agent_allocation, adaptive_metadata = (
                    self._allocate_agents_with_attribution(
                        complexity_analysis,
                        collaboration_mode,
                        episodic_prior,
                        adaptive_recommendation,
                        routing_strategy=routing_strategy,
                        adaptive_enabled_override=(
                            self.adaptive_routing_enabled
                            and effective_policy.adaptive_routing_allowed
                        ),
                        fixture_mode=effective_policy.fixture_mode,
                    )
                )

                # Step 5: Calculate performance predictions
                predictions = self._predict_performance(
                    complexity_analysis, optimization_result, agent_allocation
                )

                # Step 6: Create routing decision
                decision = RoutingDecision(
                    query_id=query_id,
                    timestamp=start_time,
                    complexity_analysis=complexity_analysis,
                    optimization_result=optimization_result,
                    routing_strategy=routing_strategy,
                    collaboration_mode=collaboration_mode,
                    agent_allocation=agent_allocation,
                    estimated_cost=predictions["cost"],
                    estimated_latency_ms=int(predictions["latency"]),
                    estimated_quality=predictions["quality"],
                    confidence_score=predictions["confidence"],
                    fallback_strategy=self._select_fallback_strategy(
                        complexity_analysis
                    ),
                    monitoring_level=self._select_monitoring_level(complexity_analysis),
                    context_requirements=self._determine_context_requirements(
                        complexity_analysis, context
                    ),
                    memory_allocation=self._allocate_memory(complexity_analysis),
                    adaptive_metadata=adaptive_metadata,
                )

                # Cache decision
                if (
                    not self.adaptive_routing_enabled
                    and not effective_policy.fixture_mode
                ):
                    self.cache_manager.cache_decision(
                        query, context, decision, strategy, constraints
                    )

                # Update metrics
                self.metrics_collector.update_metrics(decision)

                # Store in history for learning
                self.metrics_collector.add_to_history(decision)

                # Decisions alone are not outcomes.  Outcome-free learning is
                # intentionally quarantined; only record_routing_outcome may
                # update the evaluator-gated adaptive state.

                processing_time = (datetime.now() - start_time).total_seconds() * 1000
                logger.info(
                    f"Routing complete for {query_id}: {decision.collaboration_mode.value} "
                    f"mode with {decision.agent_allocation.worker_count} agents "
                    f"(processing: {processing_time:.1f}ms)"
                )

                # Update trace with final routing decision details
                if trace is not None:
                    try:
                        trace.update(
                            metadata={
                                **initial_trace_metadata,
                                "cache_hit": False,
                                "complexity_level": complexity_analysis.level.value,
                                "complexity_score": complexity_analysis.score,
                                "routing_strategy": routing_strategy.value,
                                "collaboration_mode": collaboration_mode.value,
                                "worker_count": agent_allocation.worker_count,
                                "estimated_cost": predictions["cost"],
                                "estimated_latency_ms": predictions["latency"],
                                "estimated_quality": predictions["quality"],
                                "confidence_score": predictions["confidence"],
                                "processing_time_ms": processing_time,
                            }
                        )
                    except Exception as trace_err:
                        logger.debug(
                            "trace_update_failed",
                            error=str(trace_err),
                            query_id=query_id,
                        )

                await self._routing_circuit_breaker._on_success()
                return decision

            except Exception as e:
                logger.error(f"Routing failed for {query_id}: {e}")
                await self._routing_circuit_breaker._on_failure()
                # Update trace with error metadata. Exception messages may contain
                # payloads or PII, so redact before sending to Langfuse (external SaaS).
                if trace is not None:
                    try:
                        trace.update(
                            metadata={
                                "error": redact_pii(str(e))[:300],
                                "error_type": type(e).__name__,
                            }
                        )
                    except Exception as trace_err:
                        logger.debug(
                            "trace_update_failed",
                            error=str(trace_err),
                            query_id=query_id,
                        )
                # Return fallback routing decision
                return self._create_fallback_decision(query_id, query, e)

    def _select_routing_strategy(
        self, complexity_analysis: ComplexityAnalysis, context: dict[str, Any] | None
    ) -> RoutingStrategy:
        """Select the optimal routing strategy based on analysis and context."""

        # Check for explicit strategy in context
        if context and context.get("routing_strategy"):
            return RoutingStrategy(context["routing_strategy"])

        # Use adaptive strategy if enabled and we have enough history
        if (
            self.adaptive_strategy_enabled
            and self.metrics_collector.get_history_size() > 100
        ):
            return self.metrics_collector.get_adaptive_strategy(complexity_analysis)

        # Strategy selection based on query characteristics
        if complexity_analysis.priority_level == "critical":
            return RoutingStrategy.SPEED_FIRST

        elif complexity_analysis.level == ComplexityLevel.SIMPLE:
            return RoutingStrategy.COST_EFFICIENT

        elif complexity_analysis.level == ComplexityLevel.COMPLEX:
            return RoutingStrategy.QUALITY_FOCUSED

        else:
            return self.default_strategy

    def _map_to_optimization_strategy(
        self, routing_strategy: RoutingStrategy
    ) -> OptimizationStrategy:
        """Map routing strategy to cost optimization strategy."""
        mapping = {
            RoutingStrategy.SPEED_FIRST: OptimizationStrategy.LATENCY_OPTIMIZED,
            RoutingStrategy.COST_EFFICIENT: OptimizationStrategy.COST_MINIMIZED,
            RoutingStrategy.QUALITY_FOCUSED: OptimizationStrategy.PERFORMANCE_OPTIMIZED,
            RoutingStrategy.BALANCED: OptimizationStrategy.BALANCED,
            RoutingStrategy.ADAPTIVE: OptimizationStrategy.BALANCED,
        }

        return mapping.get(routing_strategy, OptimizationStrategy.BALANCED)

    def _should_use_fast_path(self, complexity_analysis: ComplexityAnalysis) -> bool:
        """Return True if query can bypass orchestration entirely.

        Fast path criteria (based on arXiv:2604.02460):
        - SIMPLE complexity (strong model saturates capability)
        - Single domain (no parallelization benefit)
        - Single subtask (no decomposition needed)
        - Low uncertainty (<=0.3; the analyzer floors simple queries at exactly 0.3)
        - Non-critical priority (no speed requirement)
        """
        from src.core.config import get_settings

        settings = get_settings()
        if not settings.MASR_FAST_PATH_ENABLED:
            return False

        # Complexity==SIMPLE is the hard guard against false-accepts: a
        # MODERATE/COMPLEX query cannot fast-path no matter how the other
        # signals read. GENERAL-domain queries are intentionally eligible
        # (see _FAST_PATH_UNCERTAINTY_CEILING).
        return (
            complexity_analysis.level == ComplexityLevel.SIMPLE
            and len(complexity_analysis.domains) == 1
            and complexity_analysis.subtask_count == 1
            and complexity_analysis.uncertainty <= self._FAST_PATH_UNCERTAINTY_CEILING
            and "critical" not in complexity_analysis.priority_level
        )

    def _determine_collaboration_mode(
        self,
        complexity_analysis: ComplexityAnalysis,
        optimization_result: OptimizationResult,
    ) -> CollaborationMode:
        """Determine optimal agent collaboration mode."""

        # Check for fast path first (bypass all orchestration)
        if self._should_use_fast_path(complexity_analysis):
            return CollaborationMode.FAST_PATH

        # Simple queries can use direct mode
        if complexity_analysis.level == ComplexityLevel.SIMPLE:
            return CollaborationMode.DIRECT

        # Multi-domain queries benefit from parallel processing
        if len(complexity_analysis.domains) > 2:
            return CollaborationMode.PARALLEL

        # High uncertainty benefits from debate/validation
        if complexity_analysis.uncertainty > 0.7:
            return CollaborationMode.DEBATE

        # Complex single-domain queries use hierarchical
        if complexity_analysis.level == ComplexityLevel.COMPLEX:
            return CollaborationMode.HIERARCHICAL

        # Default to parallel for moderate complexity
        return CollaborationMode.PARALLEL

    async def _get_episodic_routing_prior(
        self, complexity_analysis: ComplexityAnalysis, query: str
    ) -> int | None:
        """Query episodic memory for past routing decisions on similar queries.

        Returns suggested worker_count adjustment (positive or negative) or None if
        memory is unavailable/disabled/empty.
        """
        if not self.memory_informed_routing_enabled:
            return None
        if self.episodic_memory is None:
            return None

        try:
            from src.ai_brain.memory.episodic_memory import EpisodeQuery, EventType

            # Query recent similar routing decisions
            episode_query = EpisodeQuery(
                event_types=[EventType.DECISION_MADE],
                start_time=datetime.now()
                - timedelta(days=self.memory_routing_freshness_days),
                limit=10,
            )
            episodes = await self.episodic_memory.retrieve_episodes(episode_query)

            if not episodes:
                return None

            # Extract worker_count and quality from past episodes
            adjustments: list[float] = []
            total_weight = 0.0
            for episode in episodes:
                event_data = episode.event_data or {}
                worker_count = event_data.get("worker_count")
                quality_score = episode.quality_score
                age_days = (datetime.now() - episode.timestamp).days

                if worker_count is None or quality_score is None:
                    continue

                # Freshness decay: exponential decay over freshness_days
                freshness_weight = max(
                    0.0, 1.0 - (age_days / self.memory_routing_freshness_days)
                )
                weighted_value = worker_count * quality_score * freshness_weight
                adjustments.append(weighted_value)
                total_weight += quality_score * freshness_weight

            if not adjustments or total_weight == 0:
                return None

            # Weighted average
            avg_worker_count = sum(adjustments) / total_weight
            # The prior is a nudge, not a replacement: cap the adjustment
            # (we'll apply this as a delta from the analytic baseline)
            return round(avg_worker_count)

        except Exception as e:
            # Resilient: log and return None (no memory influence)
            logger.debug(f"episodic_routing_prior failed (graceful fallback): {e}")
            return None

    async def _get_adaptive_allocation_adjustment(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
        episodic_prior: int | None,
    ) -> AdaptiveAllocationProposal | None:
        """Serialize local state refresh and proposal selection."""

        if not self.adaptive_routing_enabled or self._adaptive_engine is None:
            return None
        async with self._adaptive_state_lock:
            return await self._get_adaptive_allocation_adjustment_unlocked(
                complexity_analysis,
                collaboration_mode,
                episodic_prior,
            )

    async def _get_adaptive_allocation_adjustment_unlocked(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
        episodic_prior: int | None,
    ) -> AdaptiveAllocationProposal | None:
        """Return an attributable proposal or explicit arm-0 control.

        Readiness is based only on evaluator-eligible samples already present in
        the bandit snapshot.  Missing, incompatible, corrupt, or unavailable
        state never affects base routing and always returns the no-change arm.
        """
        if not self.adaptive_routing_enabled or self._adaptive_engine is None:
            return None

        analytic_baseline = self._infer_baseline_worker_count(
            complexity_analysis, collaboration_mode
        )
        memory_baseline = self._apply_memory_adjustment(
            analytic_baseline, episodic_prior
        )
        experiment_id = f"adaptive_allocation_{collaboration_mode.value}"

        load_status = await self._refresh_adaptive_state()
        if load_status in {
            StateLoadStatus.CORRUPT,
            StateLoadStatus.INCOMPATIBLE,
            StateLoadStatus.ERROR,
        }:
            return self._control_proposal(
                experiment_id=experiment_id,
                analytic_baseline=analytic_baseline,
                memory_baseline=memory_baseline,
                reason=f"state_{load_status.value}",
            )

        try:
            await self._ensure_adaptive_experiment(collaboration_mode)
            ready, readiness_reason = self._adaptive_engine.is_experiment_ready(
                experiment_id
            )
            if not ready:
                return self._control_proposal(
                    experiment_id=experiment_id,
                    analytic_baseline=analytic_baseline,
                    memory_baseline=memory_baseline,
                    reason=readiness_reason or "eligible_sample_readiness_failed",
                )

            decision = await self._adaptive_engine.allocate_variant(
                experiment_id,
                user_context={"collaboration_mode": collaboration_mode.value},
            )
            proposed_arm = int(decision.proposed_variant_id or decision.variant_id)
            applied_arm = int(decision.variant_id)
            proposed_count = memory_baseline + proposed_arm
            applied_count = memory_baseline + applied_arm
            control_reason = (
                ",".join(decision.safety_warnings)
                if not decision.safety_check_passed
                else None
            )
            return AdaptiveAllocationProposal(
                experiment_id=experiment_id,
                analytic_baseline_count=analytic_baseline,
                memory_baseline_count=memory_baseline,
                proposed_arm=proposed_arm,
                proposed_worker_count=proposed_count,
                applied_arm=applied_arm,
                applied_worker_count=applied_count,
                allocation_probability=decision.allocation_probability,
                ready=ready,
                safety_check_passed=decision.safety_check_passed,
                control_reason=control_reason,
                state_revision=self._adaptive_snapshot.revision,
            )
        except Exception as e:
            logger.warning(
                f"adaptive_allocation_adjustment failed (graceful fallback): {e}"
            )
            return self._control_proposal(
                experiment_id=experiment_id,
                analytic_baseline=analytic_baseline,
                memory_baseline=memory_baseline,
                reason="allocation_error",
            )

    def _adaptive_engine_config(self) -> dict[str, Any]:
        return {
            "enable_safety": True,
            "global_min_allocation": 0.05,
            "global_max_allocation": 0.70,
            "update_interval_seconds": self.config.get(
                "adaptive_routing_update_interval_seconds", 300
            ),
            "posterior_temp_enabled": self.config.get(
                "adaptive_routing_posterior_temp_enabled", True
            ),
            "posterior_temp_threshold": self.config.get(
                "adaptive_routing_posterior_temp_threshold", 150
            ),
            "posterior_temp_factor": self.config.get(
                "adaptive_routing_posterior_temp_factor", 3.0
            ),
            "rng": self._adaptive_rng,
        }

    def _adaptive_allocation_config(self) -> AllocationConfig:
        variants = [str(arm) if arm <= 0 else f"+{arm}" for arm in ADAPTIVE_ARMS]
        return AllocationConfig(
            strategy=AllocationStrategy.ADAPTIVE_BANDIT,
            initial_allocation=dict.fromkeys(variants, 1.0 / len(variants)),
            min_allocation=0.05,
            max_allocation=0.70,
            exploration_rate=0.1,
            confidence_threshold=0.95,
            update_frequency_seconds=self.config.get(
                "adaptive_routing_update_interval_seconds", 300
            ),
            enable_guardrails=True,
            performance_threshold=float(
                self.config.get("adaptive_routing_performance_threshold", 0.95)
            ),
            safety_sample_size=self.adaptive_routing_min_history,
            min_samples_per_arm=int(
                self.config.get("adaptive_routing_min_samples_per_arm", 1)
            ),
            control_variant_id="0",
        )

    async def _ensure_adaptive_experiment(
        self, collaboration_mode: CollaborationMode
    ) -> None:
        if self._adaptive_engine is None:
            raise RuntimeError("adaptive engine is unavailable")
        experiment_id = f"adaptive_allocation_{collaboration_mode.value}"
        if experiment_id in self._adaptive_engine.active_experiments:
            return
        config = self._adaptive_allocation_config()
        await self._adaptive_engine.register_experiment(
            experiment_id, list(config.initial_allocation), config
        )

    def _control_proposal(
        self,
        *,
        experiment_id: str,
        analytic_baseline: int,
        memory_baseline: int,
        reason: str,
    ) -> AdaptiveAllocationProposal:
        return AdaptiveAllocationProposal(
            experiment_id=experiment_id,
            analytic_baseline_count=analytic_baseline,
            memory_baseline_count=memory_baseline,
            proposed_arm=0,
            proposed_worker_count=memory_baseline,
            applied_arm=0,
            applied_worker_count=memory_baseline,
            allocation_probability=1.0,
            ready=False,
            safety_check_passed=False,
            control_reason=reason,
            state_revision=self._adaptive_snapshot.revision,
        )

    def _infer_baseline_worker_count(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
    ) -> int:
        """Infer analytic baseline worker_count for a given collaboration_mode.

        This is a heuristic to reconstruct the baseline that _allocate_agents would
        compute, so we can apply bandit deltas on top of it.
        """
        if collaboration_mode in {
            CollaborationMode.FAST_PATH,
            CollaborationMode.DIRECT,
        }:
            return 1
        if collaboration_mode == CollaborationMode.PARALLEL:
            return int(
                min(len(complexity_analysis.domains) + 1, self.max_parallel_workers)
            )
        if collaboration_mode == CollaborationMode.HIERARCHICAL:
            return int(
                min(complexity_analysis.subtask_count, self.max_agents_per_query)
            )
        if collaboration_mode == CollaborationMode.DEBATE:
            return 3  # Fixed
        return 5

    async def record_routing_outcome(
        self, outcome: RoutingOutcome
    ) -> OutcomeApplicationResult:
        """Idempotently apply a typed evaluator-qualified outcome."""

        eligibility = self._outcome_eligibility_policy.assess(outcome)
        evaluated = outcome.with_eligibility(eligibility)
        if not self.adaptive_routing_enabled or self._adaptive_engine is None:
            self._observe_adaptive_effective_state()
            return OutcomeApplicationResult(
                status=OutcomeApplicationStatus.INELIGIBLE_RECORDED,
                outcome=evaluated,
                learning_updated=False,
                reason="adaptive_routing_disabled",
            )
        async with self._adaptive_state_lock:
            return await self._record_routing_outcome_unlocked(evaluated)

    async def _record_routing_outcome_unlocked(
        self, evaluated: RoutingOutcome
    ) -> OutcomeApplicationResult:
        """Apply one outcome while holding the process-local state lock."""

        eligibility = evaluated.eligibility
        for attempt in range(self._adaptive_conflict_retries + 1):
            load_status = await self._refresh_adaptive_state()
            if load_status in {
                StateLoadStatus.CORRUPT,
                StateLoadStatus.INCOMPATIBLE,
            }:
                return OutcomeApplicationResult(
                    status=OutcomeApplicationStatus.INCOMPATIBLE_STATE,
                    outcome=evaluated,
                    learning_updated=False,
                    reason=f"state_{load_status.value}",
                )
            if load_status == StateLoadStatus.ERROR:
                return OutcomeApplicationResult(
                    status=OutcomeApplicationStatus.STORE_ERROR,
                    outcome=evaluated,
                    learning_updated=False,
                    retryable=True,
                    reason="state_store_error",
                )

            base = self._adaptive_snapshot
            try:
                next_snapshot = await self._apply_outcome_to_snapshot(base, evaluated)
            except Exception as exc:
                logger.warning(
                    "adaptive_outcome_application_failed",
                    error=type(exc).__name__,
                )
                await self._restore_snapshot(base)
                self._observe_adaptive_effective_state()
                return OutcomeApplicationResult(
                    status=OutcomeApplicationStatus.INCOMPATIBLE_STATE,
                    outcome=evaluated,
                    learning_updated=False,
                    reason="outcome_application_failed",
                )

            write = await self._adaptive_state_store.compare_and_set(
                expected_revision=base.revision,
                snapshot=next_snapshot,
                outcome_id=evaluated.outcome_id,
            )
            if write.status == StateWriteStatus.APPLIED:
                self._adaptive_snapshot = next_snapshot
                self._adaptive_store_healthy = True
                self._observe_adaptive_effective_state()
                return OutcomeApplicationResult(
                    status=(
                        OutcomeApplicationStatus.APPLIED
                        if eligibility.eligible
                        else OutcomeApplicationStatus.INELIGIBLE_RECORDED
                    ),
                    outcome=evaluated,
                    learning_updated=eligibility.eligible,
                    reason=eligibility.reason.value,
                )
            if write.status == StateWriteStatus.DUPLICATE:
                await self._restore_snapshot(base)
                self._observe_adaptive_effective_state()
                return OutcomeApplicationResult(
                    status=OutcomeApplicationStatus.DUPLICATE,
                    outcome=evaluated,
                    learning_updated=False,
                    duplicate=True,
                    reason="duplicate_outcome_id",
                )
            if write.status == StateWriteStatus.CONFLICT and attempt < (
                self._adaptive_conflict_retries
            ):
                await self._wait_for_adaptive_conflict_retry(attempt)
                continue

            await self._restore_snapshot(base)
            self._adaptive_store_healthy = False
            self._observe_adaptive_effective_state()
            if write.status == StateWriteStatus.CONFLICT:
                status = OutcomeApplicationStatus.CONFLICT_EXHAUSTED
            elif write.status in {
                StateWriteStatus.CORRUPT,
                StateWriteStatus.INCOMPATIBLE,
            }:
                status = OutcomeApplicationStatus.INCOMPATIBLE_STATE
            else:
                status = OutcomeApplicationStatus.STORE_ERROR
            return OutcomeApplicationResult(
                status=status,
                outcome=evaluated,
                learning_updated=False,
                retryable=status
                in {
                    OutcomeApplicationStatus.CONFLICT_EXHAUSTED,
                    OutcomeApplicationStatus.STORE_ERROR,
                },
                reason=write.reason or write.status.value,
            )

        raise AssertionError("bounded conflict loop must return")

    async def _wait_for_adaptive_conflict_retry(self, attempt: int) -> None:
        """Apply bounded exponential jitter before reloading conflicted state."""

        if self._adaptive_conflict_backoff_seconds == 0.0:
            return
        jitter = 0.5 + float(self._adaptive_rng.random())
        delay = min(
            0.25,
            self._adaptive_conflict_backoff_seconds * (2**attempt) * jitter,
        )
        await asyncio.sleep(delay)

    async def _refresh_adaptive_state(self) -> StateLoadStatus:
        result = await self._adaptive_state_store.load()
        self._adaptive_state_status = result.status
        if result.status == StateLoadStatus.MISSING:
            self._adaptive_engine = AdaptiveAllocationEngine(
                self._adaptive_engine_config()
            )
            self._adaptive_snapshot = empty_adaptive_snapshot(
                schema_version=self.adaptive_schema_version,
                policy_version=self.adaptive_policy_version,
            )
            self._adaptive_store_healthy = True
            self._observe_adaptive_effective_state()
            return result.status
        if result.status != StateLoadStatus.LOADED or result.snapshot is None:
            self._adaptive_store_healthy = False
            self._observe_adaptive_effective_state()
            return result.status
        if (
            result.snapshot.schema_version != self.adaptive_schema_version
            or result.snapshot.policy_version != self.adaptive_policy_version
        ):
            self._adaptive_store_healthy = False
            self._adaptive_state_status = StateLoadStatus.INCOMPATIBLE
            self._observe_adaptive_effective_state()
            return StateLoadStatus.INCOMPATIBLE
        try:
            await self._restore_snapshot(result.snapshot)
        except (KeyError, TypeError, ValueError):
            self._adaptive_store_healthy = False
            self._adaptive_state_status = StateLoadStatus.INCOMPATIBLE
            self._observe_adaptive_effective_state()
            return StateLoadStatus.INCOMPATIBLE
        self._adaptive_store_healthy = True
        self._observe_adaptive_effective_state()
        return result.status

    async def initialize_adaptive_state(self) -> StateLoadStatus:
        """Restore durable adaptive state without making base routing depend on it."""

        if not self.adaptive_routing_enabled or self._adaptive_engine is None:
            return StateLoadStatus.MISSING
        async with self._adaptive_state_lock:
            return await self._refresh_adaptive_state()

    async def close(self) -> None:
        """Release process-local router resources owned by the runtime."""

        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self.cache_manager.clear()

    async def _restore_snapshot(self, snapshot: AdaptiveStateSnapshot) -> None:
        self._adaptive_engine = AdaptiveAllocationEngine(self._adaptive_engine_config())
        for experiment in snapshot.experiments:
            mode_value = experiment.experiment_id.removeprefix("adaptive_allocation_")
            mode = CollaborationMode(mode_value)
            await self._ensure_adaptive_experiment(mode)
            self._adaptive_engine.restore_experiment_state(
                experiment.experiment_id,
                experiment.to_dict(),
            )
        self._mode_quality_baselines = {
            CollaborationMode(mode): value
            for mode, value in snapshot.mode_quality_baselines
        }
        self._adaptive_snapshot = snapshot

    async def _apply_outcome_to_snapshot(
        self,
        base: AdaptiveStateSnapshot,
        outcome: RoutingOutcome,
    ) -> AdaptiveStateSnapshot:
        eligible_count = base.eligible_outcome_count
        ineligible_count = base.ineligible_outcome_count

        if outcome.eligibility.eligible:
            if outcome.quality_score is None:
                raise ValueError("eligible outcome must carry measured quality")
            await self._ensure_adaptive_experiment(outcome.collaboration_mode)
            engine = self._adaptive_engine
            if engine is None:
                raise RuntimeError("adaptive engine is unavailable")
            experiment_id = f"adaptive_allocation_{outcome.collaboration_mode.value}"
            quality_baseline = self._mode_quality_baselines.get(
                outcome.collaboration_mode, 0.75
            )
            advantage = outcome.quality_score - quality_baseline
            self._mode_quality_baselines[outcome.collaboration_mode] = float(
                0.95 * quality_baseline + 0.05 * outcome.quality_score
            )
            reward = float(np.clip(advantage + 0.5, 0.0, 1.0))
            variant_id = (
                str(outcome.applied_arm)
                if outcome.applied_arm <= 0
                else f"+{outcome.applied_arm}"
            )
            await engine.record_outcome(experiment_id, variant_id, reward)
            eligible_count += 1
        else:
            ineligible_count += 1

        engine = self._adaptive_engine
        if engine is None:
            raise RuntimeError("adaptive engine is unavailable")
        experiments = tuple(
            AdaptiveExperimentSnapshot.from_dict(item)
            for item in engine.export_experiment_state()
        )
        return base.next_revision(
            experiments=experiments,
            mode_quality_baselines=tuple(
                sorted(
                    (mode.value, value)
                    for mode, value in self._mode_quality_baselines.items()
                )
            ),
            eligible_outcome_count=eligible_count,
            ineligible_outcome_count=ineligible_count,
            processed_outcome_count=base.processed_outcome_count + 1,
        )

    def _apply_memory_adjustment(
        self, analytic_count: int, episodic_prior: int | None
    ) -> int:
        """Apply bounded memory-informed adjustment to analytic worker_count.

        Args:
            analytic_count: The analytic baseline worker count
            episodic_prior: The raw prior from episodic memory (or None)

        Returns:
            Adjusted worker_count, bounded to ± max_worker_adjust from baseline
        """
        if episodic_prior is None:
            return analytic_count

        # Compute the delta (capped)
        delta: int = episodic_prior - analytic_count
        max_adjust: int = self.memory_routing_max_worker_adjust
        capped_delta: int = max(-max_adjust, min(max_adjust, delta))

        adjusted: int = analytic_count + capped_delta
        # Ensure we never go below 1
        result: int = max(1, adjusted)
        return result

    def _apply_adaptive_adjustment(
        self, memory_adjusted_count: int, adaptive_recommendation: int | None
    ) -> int:
        """Apply bounded adaptive routing adjustment to memory-adjusted worker_count.

        Args:
            memory_adjusted_count: Worker count after memory adjustment (or analytic baseline)
            adaptive_recommendation: The raw recommendation from adaptive engine (or None)

        Returns:
            Final adjusted worker_count, bounded to ± adaptive_max_worker_adjust from baseline
        """
        if adaptive_recommendation is None:
            return memory_adjusted_count

        # Compute the delta (capped)
        delta: int = adaptive_recommendation - memory_adjusted_count
        max_adjust: int = self.adaptive_routing_max_worker_adjust
        capped_delta: int = max(-max_adjust, min(max_adjust, delta))

        adjusted: int = memory_adjusted_count + capped_delta
        # Ensure we never go below 1
        result: int = max(1, adjusted)

        # Log structured event when adaptation changes allocation
        if capped_delta != 0:
            logger.info(
                "adaptive_routing_adjustment_applied",
                memory_adjusted_baseline=memory_adjusted_count,
                adaptive_recommendation=adaptive_recommendation,
                adaptive_delta=capped_delta,
                final_worker_count=result,
            )

        return result

    def _get_strategy_budget(
        self, strategy: RoutingStrategy, mode: CollaborationMode
    ) -> int:
        """Return hard worker-count cap for strategy+mode combination.

        Budgets based on Anthropic research (arXiv:2604.02460):
        - Simple fact-finding: 1 agent
        - Comparisons: 2-4 agents
        - Complex research: 10+ agents

        Returns:
            Hard cap on worker_count for this strategy+mode combo
        """
        budgets = {
            RoutingStrategy.COST_EFFICIENT: {
                CollaborationMode.FAST_PATH: 1,
                CollaborationMode.DIRECT: 2,  # 1 worker + 1 supervisor
                CollaborationMode.PARALLEL: 2,
                CollaborationMode.HIERARCHICAL: 2,
                CollaborationMode.DEBATE: 3,
                CollaborationMode.ENSEMBLE: 2,
            },
            RoutingStrategy.SPEED_FIRST: {
                CollaborationMode.FAST_PATH: 1,
                CollaborationMode.DIRECT: 1,
                CollaborationMode.PARALLEL: 3,  # Bounded concurrency
                CollaborationMode.HIERARCHICAL: 4,
                CollaborationMode.DEBATE: 3,
                CollaborationMode.ENSEMBLE: 3,
            },
            RoutingStrategy.QUALITY_FOCUSED: {
                CollaborationMode.FAST_PATH: 1,  # Quality mode doesn't use fast path in practice
                CollaborationMode.DIRECT: 2,
                CollaborationMode.PARALLEL: 4,
                CollaborationMode.HIERARCHICAL: 10,  # Complex research
                CollaborationMode.DEBATE: 3,  # Fixed
                CollaborationMode.ENSEMBLE: 5,
            },
            RoutingStrategy.BALANCED: {
                CollaborationMode.FAST_PATH: 1,
                CollaborationMode.DIRECT: 2,
                CollaborationMode.PARALLEL: 3,
                CollaborationMode.HIERARCHICAL: 6,
                CollaborationMode.DEBATE: 3,
                CollaborationMode.ENSEMBLE: 3,
            },
            RoutingStrategy.ADAPTIVE: {
                # Same as BALANCED (adaptive adjusts within bounds)
                CollaborationMode.FAST_PATH: 1,
                CollaborationMode.DIRECT: 2,
                CollaborationMode.PARALLEL: 3,
                CollaborationMode.HIERARCHICAL: 6,
                CollaborationMode.DEBATE: 3,
                CollaborationMode.ENSEMBLE: 3,
            },
        }

        strategy_budgets = budgets.get(strategy, budgets[RoutingStrategy.BALANCED])
        return strategy_budgets.get(mode, 10)  # Global max fallback

    def _allocate_agents_with_attribution(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
        episodic_prior: int | None = None,
        adaptive_recommendation: AdaptiveAllocationProposal | int | None = None,
        routing_strategy: RoutingStrategy | None = None,
        adaptive_enabled_override: bool | None = None,
        fixture_mode: bool = False,
    ) -> tuple[AgentAllocation, AdaptiveDecisionMetadata]:
        """Allocate agents and retain literal proposal/application attribution."""

        strategy = routing_strategy or self.default_strategy
        analytic_baseline = self._infer_baseline_worker_count(
            complexity_analysis, collaboration_mode
        )
        memory_baseline = self._apply_memory_adjustment(
            analytic_baseline, episodic_prior
        )
        proposal = (
            adaptive_recommendation
            if isinstance(adaptive_recommendation, AdaptiveAllocationProposal)
            else None
        )
        budget_cap = self._get_strategy_budget(strategy, collaboration_mode)
        system_min, system_max = self._allocation_system_bounds(collaboration_mode)

        bounded_proposal_count = memory_baseline
        if proposal is not None:
            proposed_delta = proposal.applied_worker_count - memory_baseline
            bounded_delta = max(
                -self.adaptive_routing_max_worker_adjust,
                min(self.adaptive_routing_max_worker_adjust, proposed_delta),
            )
            bounded_proposal_count = max(1, memory_baseline + bounded_delta)
        safety_clamped = proposal is not None and not proposal.safety_check_passed
        budget_clamped = proposal is not None and bounded_proposal_count > budget_cap
        system_clamped = (
            proposal is not None
            and not system_min <= bounded_proposal_count <= system_max
        )
        fixed_mode = collaboration_mode in {
            CollaborationMode.FAST_PATH,
            CollaborationMode.DIRECT,
            CollaborationMode.DEBATE,
        }

        if proposal is None:
            recommendation = (
                adaptive_recommendation
                if isinstance(adaptive_recommendation, int)
                else None
            )
        elif safety_clamped or budget_clamped or system_clamped or fixed_mode:
            recommendation = memory_baseline
        else:
            recommendation = bounded_proposal_count

        allocation = self._allocate_agents(
            complexity_analysis,
            collaboration_mode,
            episodic_prior,
            recommendation,
            routing_strategy=strategy,
        )

        enabled = (
            self.adaptive_routing_enabled
            if adaptive_enabled_override is None
            else adaptive_enabled_override
        )
        proposed_arm = proposal.proposed_arm if proposal else 0
        proposed_count = proposal.proposed_worker_count if proposal else memory_baseline
        probability = proposal.allocation_probability if proposal else 1.0
        ready = proposal.ready if proposal else False
        state_revision = proposal.state_revision if proposal else 0
        final_delta = allocation.worker_count - memory_baseline
        applied_arm = 0
        if (
            proposal is not None
            and not safety_clamped
            and not budget_clamped
            and not system_clamped
            and not fixed_mode
            and final_delta in ADAPTIVE_ARMS
        ):
            # Attribute the arm that actually executed after the configured
            # adjustment cap, never the pre-cap proposal.
            applied_arm = final_delta

        control_reason = proposal.control_reason if proposal else None
        if fixed_mode and enabled:
            control_reason = "collaboration_mode_uses_fixed_allocation"
        elif budget_clamped:
            control_reason = "proposal_exceeds_strategy_budget"
        elif system_clamped:
            control_reason = "proposal_exceeds_system_bounds"

        if fixture_mode:
            status = AdaptiveRoutingStatus.FIXTURE_OFF
            control_reason = "fixture_policy_forced_off"
        elif not enabled:
            status = AdaptiveRoutingStatus.DISABLED
            control_reason = "adaptive_routing_disabled"
        elif proposal is None or not proposal.ready:
            if control_reason and (
                "state_" in control_reason or "error" in control_reason
            ):
                status = AdaptiveRoutingStatus.DEGRADED
            else:
                status = AdaptiveRoutingStatus.COLD
        elif applied_arm == 0:
            status = AdaptiveRoutingStatus.CONTROL
        else:
            status = AdaptiveRoutingStatus.ACTIVE

        metadata = AdaptiveDecisionMetadata(
            schema_version=self.adaptive_schema_version,
            policy_version=self.adaptive_policy_version,
            state_revision=state_revision,
            status=status,
            enabled=enabled,
            ready=ready,
            analytic_baseline_count=analytic_baseline,
            memory_baseline_count=memory_baseline,
            proposed_arm=proposed_arm,
            proposed_worker_count=proposed_count,
            proposal_probability=probability,
            safety_clamped=safety_clamped,
            budget_clamped=budget_clamped,
            system_clamped=system_clamped,
            final_worker_count=allocation.worker_count,
            applied_arm=applied_arm,
            control_reason=control_reason,
        )
        return allocation, metadata

    def _allocation_system_bounds(
        self, collaboration_mode: CollaborationMode
    ) -> tuple[int, int]:
        if collaboration_mode == CollaborationMode.PARALLEL:
            return 1, self.max_parallel_workers
        if collaboration_mode == CollaborationMode.HIERARCHICAL:
            return 1, self.max_agents_per_query
        if collaboration_mode == CollaborationMode.ENSEMBLE:
            return 3, 7
        if collaboration_mode == CollaborationMode.DEBATE:
            return 3, 3
        return 1, 1

    def _allocate_agents(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
        episodic_prior: int | None = None,
        adaptive_recommendation: int | None = None,
        routing_strategy: RoutingStrategy | None = None,
    ) -> AgentAllocation:
        """Determine optimal agent allocation with supervisor-based hierarchical routing.

        Args:
            complexity_analysis: Query complexity analysis
            collaboration_mode: Determined collaboration mode
            episodic_prior: Optional episodic memory prior for worker_count (raw value)
            adaptive_recommendation: Optional adaptive engine recommendation (raw value)
        """

        # Get supervisor types based on domains
        supervisor_types = self._get_domain_supervisor_types(
            complexity_analysis.domains
        )
        primary_supervisor = supervisor_types[0] if supervisor_types else "research"

        # Get budget cap for this strategy+mode combination (enforced AFTER adjustments)
        budget_cap = self._get_strategy_budget(
            routing_strategy or self.default_strategy, collaboration_mode
        )

        # Base allocation by collaboration mode
        if collaboration_mode == CollaborationMode.FAST_PATH:
            # Fast path: no supervisor, single LLM call
            return AgentAllocation(
                supervisor_type=primary_supervisor,  # Placeholder (not used)
                worker_count=1,
                worker_types=[],  # No workers (direct LLM call)
                max_parallel=1,
                timeout_seconds=SHORT_TIMEOUT,
                retry_attempts=MIN_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.DIRECT:
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=1,
                worker_types=self._get_domain_worker_types(complexity_analysis.domains),
                max_parallel=DIRECT_MODE_PARALLELISM,
                timeout_seconds=SHORT_TIMEOUT,
                retry_attempts=MIN_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.PARALLEL:
            analytic_count = min(
                len(complexity_analysis.domains) + 1, self.max_parallel_workers
            )
            # Sequential composition: memory first, then adaptive, THEN budget cap
            memory_adjusted = self._apply_memory_adjustment(
                analytic_count, episodic_prior
            )
            adaptive_adjusted = self._apply_adaptive_adjustment(
                memory_adjusted, adaptive_recommendation
            )
            # Enforce budget cap AFTER all adjustments
            worker_count = min(adaptive_adjusted, budget_cap, self.max_parallel_workers)
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                worker_types=self._get_domain_worker_types(complexity_analysis.domains),
                max_parallel=worker_count,
                timeout_seconds=MEDIUM_TIMEOUT,
                retry_attempts=DEFAULT_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.HIERARCHICAL:
            analytic_count = min(
                complexity_analysis.subtask_count, self.max_agents_per_query
            )
            # Sequential composition: memory first, then adaptive, THEN budget cap
            memory_adjusted = self._apply_memory_adjustment(
                analytic_count, episodic_prior
            )
            adaptive_adjusted = self._apply_adaptive_adjustment(
                memory_adjusted, adaptive_recommendation
            )
            # Enforce budget cap AFTER all adjustments
            worker_count = min(adaptive_adjusted, budget_cap, self.max_agents_per_query)
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                worker_types=self._get_specialized_worker_types(complexity_analysis),
                max_parallel=min(worker_count, LOW_PARALLELISM),
                timeout_seconds=DEFAULT_AGENT_TIMEOUT,
                retry_attempts=MAX_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.DEBATE:
            # DEBATE uses three fixed roles; the budget can only cap below that.
            debate_roles = ["analyst", "critic", "synthesizer"]
            worker_count = min(len(debate_roles), budget_cap)
            if worker_count < len(debate_roles):
                logger.warning(
                    "debate_budget_below_role_count",
                    budget_cap=budget_cap,
                    role_count=len(debate_roles),
                    routing_strategy=str(routing_strategy),
                )
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                # Keep roles and count in lockstep so a sub-3 budget can never
                # leave the supervisor expecting a role it was not allocated.
                worker_types=debate_roles[:worker_count],
                max_parallel=LOW_PARALLELISM,
                timeout_seconds=LONG_TIMEOUT,
                retry_attempts=DEFAULT_RETRY_ATTEMPTS,
            )

        else:  # ENSEMBLE
            analytic_count = 5
            # Sequential composition: memory first, then adaptive, THEN budget cap
            memory_adjusted = self._apply_memory_adjustment(
                analytic_count, episodic_prior
            )
            adaptive_adjusted = self._apply_adaptive_adjustment(
                memory_adjusted, adaptive_recommendation
            )
            # Enforce budget cap AFTER all adjustments; ensemble range is 3-7 but budget may restrict further
            worker_count = min(adaptive_adjusted, budget_cap)
            worker_count = max(3, min(worker_count, 7))  # Clamp to ensemble range
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                worker_types=self._get_domain_worker_types(complexity_analysis.domains),
                max_parallel=HIGH_PARALLELISM,
                timeout_seconds=MEDIUM_TIMEOUT,
                retry_attempts=MIN_RETRY_ATTEMPTS,
            )

    def _get_domain_supervisor_types(self, domains: Any) -> list[str]:
        """Get supervisor types based on identified domains (enhanced for hierarchical routing)."""
        supervisor_types = []

        domain_supervisors = {
            "research": "research",
            "content": "content",
            "analytics": "analytics",
            "finance": "finance",
            "service": "service",
            "multimodal": "content",  # Fallback to content supervisor for multimodal
        }

        for domain in domains:
            domain_name = domain.value if hasattr(domain, "value") else str(domain)
            supervisor_type = domain_supervisors.get(
                domain_name, "research"
            )  # Default to research
            if supervisor_type not in supervisor_types:
                supervisor_types.append(supervisor_type)

        # Ensure we have at least one supervisor
        if not supervisor_types:
            supervisor_types = ["research"]

        return supervisor_types

    def _get_domain_worker_types(self, domains: Any) -> list[str]:
        """Get worker types based on identified domains (legacy method for backward compatibility)."""
        worker_types = []

        domain_workers = {
            "research": "research_specialist",
            "content": "content_specialist",
            "analytics": "analytics_specialist",
            "finance": "financial_analysis",
            "service": "service_specialist",
            "multimodal": "multimodal_specialist",
        }

        for domain in domains:
            domain_name = domain.value if hasattr(domain, "value") else str(domain)
            worker_type = domain_workers.get(domain_name, "general_specialist")
            if worker_type not in worker_types:
                worker_types.append(worker_type)

        # Ensure we have at least one worker
        if not worker_types:
            worker_types = ["general_specialist"]

        return worker_types

    def _get_specialized_worker_types(self, complexity_analysis: Any) -> list[str]:
        """Get specialized worker types for hierarchical mode."""
        worker_types = []

        # Add based on reasoning types needed
        reasoning_workers = {
            "analytical": "analysis_specialist",
            "logical": "logic_specialist",
            "comparative": "comparison_specialist",
            "synthetic": "synthesis_specialist",
            "evaluative": "evaluation_specialist",
        }

        for reasoning_type in complexity_analysis.reasoning_types:
            if reasoning_type in reasoning_workers:
                worker_types.append(reasoning_workers[reasoning_type])

        # Add domain specialists
        worker_types.extend(self._get_domain_worker_types(complexity_analysis.domains))

        # Always include a validator for complex queries
        worker_types.append("validation_specialist")

        return worker_types[: self.max_agents_per_query]

    def _predict_performance(
        self,
        complexity_analysis: ComplexityAnalysis,
        optimization_result: OptimizationResult,
        agent_allocation: AgentAllocation,
    ) -> dict[str, float]:
        """Predict performance metrics for the routing decision."""

        # Base predictions from optimization
        if optimization_result.estimated_cost is None:
            return {"cost": 0.0, "latency": 0.0, "quality": 0.0, "confidence": 0.0}

        base_cost = optimization_result.estimated_cost.cost_per_request
        base_latency = optimization_result.estimated_cost.latency_estimate_ms
        base_quality = optimization_result.estimated_cost.quality_score

        # Adjust for agent overhead
        agent_overhead_factor = 1 + (agent_allocation.worker_count - 1) * 0.1
        coordination_overhead = 50 * agent_allocation.worker_count  # ms per agent

        predicted_cost = base_cost * agent_overhead_factor
        predicted_latency = base_latency + coordination_overhead
        predicted_quality = min(
            base_quality + (agent_allocation.worker_count * 0.05), 1.0
        )

        # Confidence based on analysis uncertainty
        confidence = 1.0 - complexity_analysis.uncertainty

        return {
            "cost": predicted_cost,
            "latency": predicted_latency,
            "quality": predicted_quality,
            "confidence": confidence,
        }

    def _select_fallback_strategy(self, complexity_analysis: Any) -> str:
        """Select appropriate fallback strategy."""
        if complexity_analysis.priority_level == "critical":
            return "immediate_fallback"
        elif complexity_analysis.level == ComplexityLevel.SIMPLE:
            return "retry_with_simpler_model"
        else:
            return "graceful_degradation"

    def _select_monitoring_level(self, complexity_analysis: Any) -> str:
        """Select monitoring level based on complexity."""
        if (
            complexity_analysis.level == ComplexityLevel.COMPLEX
            or complexity_analysis.uncertainty > 0.7
        ):
            return "detailed"
        else:
            return "standard"

    def _determine_context_requirements(
        self, complexity_analysis: Any, context: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Determine context preservation requirements."""
        requirements: dict[str, Any] = {}

        # Memory requirements based on complexity
        if complexity_analysis.level != ComplexityLevel.SIMPLE:
            requirements["preserve_conversation"] = True
            requirements["max_context_tokens"] = (
                complexity_analysis.estimated_tokens * 2
            )

        # Session requirements
        if context and context.get("session_id"):
            requirements["session_continuity"] = True
            requirements["session_id"] = context["session_id"]

        return requirements

    def _allocate_memory(self, complexity_analysis: Any) -> dict[str, int]:
        """Allocate memory resources based on complexity."""
        allocation: dict[str, int] = {}

        # Working memory allocation
        allocation["working_memory_mb"] = 100 + (complexity_analysis.subtask_count * 50)

        # Context window allocation
        allocation["context_tokens"] = complexity_analysis.estimated_tokens * 3

        # Cache allocation
        allocation["cache_mb"] = 50 + (len(complexity_analysis.domains) * 25)

        return allocation

    def _create_fallback_decision(
        self, query_id: str, query: str, error: Exception
    ) -> RoutingDecision:
        """Create a safe fallback routing decision when routing fails."""
        logger.warning(f"Creating fallback decision for {query_id} due to: {error}")

        # Simple fallback analysis
        from .query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
            ComplexityLevel,
        )

        fallback_analysis = ComplexityAnalysis(
            score=0.5,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=[],
            subtask_count=1,
            uncertainty=0.8,
            reasoning_types=[],
            recommended_agents={"general": 1},
            estimated_tokens=DEFAULT_ESTIMATED_TOKENS,
        )

        # Simple fallback optimization
        from .cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )

        fallback_model = ModelSpec(
            name="llama-3.3-70b",
            provider="ollama",
            tier=ModelTier.STANDARD,
            cost_per_1k_tokens=0.0008,
            avg_latency_ms=30,
            context_window=128000,
            quality_score=0.75,
        )

        fallback_estimate = CostEstimate(
            model_name="llama-3.3-70b",
            estimated_tokens=1000,
            cost_per_request=0.0008,
            total_monthly_cost=80.0,
            latency_estimate_ms=30,
            quality_score=0.75,
            confidence=0.5,
        )

        fallback_optimization = OptimizationResult(
            primary_model=fallback_model,
            estimated_cost=fallback_estimate,
            reasoning="Fallback routing due to analysis failure",
        )

        return RoutingDecision(
            query_id=query_id,
            timestamp=datetime.now(),
            complexity_analysis=fallback_analysis,
            optimization_result=fallback_optimization,
            routing_strategy=self.default_strategy,
            collaboration_mode=CollaborationMode.DIRECT,
            agent_allocation=AgentAllocation(
                supervisor_type="general", worker_count=1, worker_types=["general"]
            ),
            estimated_cost=0.0008,
            estimated_latency_ms=100,
            estimated_quality=0.7,
            confidence_score=0.3,
            fallback_strategy="error_recovery",
            monitoring_level="detailed",
        )

    async def get_metrics(self) -> RoutingMetrics:
        """Get current routing metrics."""
        return self.metrics_collector.get_metrics()

    def _adaptive_experiment_readiness(self) -> dict[str, bool]:
        """Return readiness for every experiment observed by this process."""

        experiment_ids = {
            experiment.experiment_id
            for experiment in self._adaptive_snapshot.experiments
        }
        if self._adaptive_engine is not None:
            experiment_ids.update(self._adaptive_engine.active_experiments)

        readiness: dict[str, bool] = {}
        for experiment_id in sorted(experiment_ids):
            readiness[experiment_id] = (
                self._adaptive_engine is not None
                and self._adaptive_engine.is_experiment_ready(experiment_id)[0]
            )
        return readiness

    def _adaptive_effective_state(
        self,
        experiment_readiness: dict[str, bool] | None = None,
    ) -> tuple[AdaptiveRoutingStatus, str, bool]:
        """Calculate the process-local effective state without store I/O."""

        readiness = (
            experiment_readiness
            if experiment_readiness is not None
            else self._adaptive_experiment_readiness()
        )
        # A global active claim requires every experiment observed/configured
        # in this process to be ready. An empty experiment set remains cold.
        ready = bool(readiness) and all(readiness.values())

        if self.fixture_mode:
            return AdaptiveRoutingStatus.FIXTURE_OFF, "not_used", False
        if not self.adaptive_routing_enabled:
            return AdaptiveRoutingStatus.DISABLED, "not_used", False
        if not self._adaptive_store_healthy or self._adaptive_state_status in {
            StateLoadStatus.CORRUPT,
            StateLoadStatus.ERROR,
            StateLoadStatus.INCOMPATIBLE,
        }:
            return AdaptiveRoutingStatus.DEGRADED, "degraded", False
        if ready:
            return AdaptiveRoutingStatus.ACTIVE, "healthy", True
        return AdaptiveRoutingStatus.COLD, "healthy", False

    def _observe_adaptive_effective_state(self) -> None:
        """Publish bounded telemetry at a real local state boundary."""

        effective_state, _, _ = self._adaptive_effective_state()
        observe_effective_state(effective_state.value)

    async def get_adaptive_status(self) -> dict[str, Any]:
        """Return measured adaptive state without touching the state store."""

        snapshot = self._adaptive_snapshot
        per_arm_counts = {str(arm): 0 for arm in ADAPTIVE_ARMS}
        for experiment in snapshot.experiments:
            for arm, count in zip(
                experiment.ordered_arms,
                experiment.arm_counts,
                strict=True,
            ):
                per_arm_counts[str(arm)] += count

        experiment_readiness = self._adaptive_experiment_readiness()
        effective_state, store_health, ready = self._adaptive_effective_state(
            experiment_readiness
        )

        return {
            "effective_state": effective_state.value,
            "enabled": self.adaptive_routing_enabled and not self.fixture_mode,
            "ready": ready,
            "schema_version": self.adaptive_schema_version,
            "policy_version": self.adaptive_policy_version,
            "state_revision": snapshot.revision,
            "state_load_status": self._adaptive_state_status.value,
            "store_health": store_health,
            "eligible_outcome_count": snapshot.eligible_outcome_count,
            "ineligible_outcome_count": snapshot.ineligible_outcome_count,
            "duplicate_outcome_count": snapshot.duplicate_outcome_count,
            "processed_outcome_count": snapshot.processed_outcome_count,
            "fallback_count": snapshot.fallback_count,
            "per_arm_counts": per_arm_counts,
            "experiment_readiness": experiment_readiness,
        }

    async def health_check(self) -> HealthCheckDict:
        """Perform health check on MASR components."""
        metrics = self.metrics_collector.get_metrics()
        health: HealthCheckDict = {
            "status": "healthy",
            "components": {
                "complexity_analyzer": "healthy",
                "cost_optimizer": "healthy",
                "decision_cache": f"{self.cache_manager.get_cache_size()} entries",
                "routing_history": f"{self.metrics_collector.get_history_size()} decisions",
            },
            "metrics": {
                "total_requests": metrics.total_requests,
                "cache_hit_rate": "N/A",
                "avg_routing_time_ms": "N/A",
            },
        }
        health["metrics"]["adaptive_routing"] = await self.get_adaptive_status()

        return health


__all__ = [
    "AgentAllocation",
    "CollaborationMode",
    "MASRouter",
    "RoutingDecision",
    "RoutingMetrics",
    "RoutingStrategy",
]
