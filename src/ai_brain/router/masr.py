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

from .cost_optimizer import CostOptimizer, OptimizationResult, OptimizationStrategy
from .query_analyzer import ComplexityAnalysis, ComplexityLevel, QueryComplexityAnalyzer
from .routing_cache import RoutingCacheManager
from .routing_metrics import RoutingMetricsCollector
from .routing_types import (
    AgentAllocation,
    CollaborationMode,
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


class MASRouter:
    """
    Multi-Agent System Router - The central intelligence of Cerebro.

    Combines complexity analysis with cost optimization to make intelligent
    routing decisions that balance performance, cost, and quality based on
    query requirements and system constraints.

    Now supports dynamic model configuration for flexible routing.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        model_config_manager: ModelConfigManager | None = None,
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

        # Routing configuration
        self.enable_adaptive_routing = self.config.get("enable_adaptive", True)

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
        self._adaptive_engine: AdaptiveAllocationEngine | None = None
        # Per-mode quality baselines for advantage reward computation (EMA)
        self._mode_quality_baselines: dict[CollaborationMode, float] = {}
        if self.adaptive_routing_enabled:
            self._adaptive_engine = AdaptiveAllocationEngine(
                {
                    "enable_safety": True,
                    "global_min_allocation": 0.05,
                    "global_max_allocation": 0.70,
                    "update_interval_seconds": self.config.get(
                        "adaptive_routing_update_interval_seconds", 300
                    ),
                    # Convergence lever: sharpen Thompson posteriors after
                    # warm-up so exploitation ramps once arms are estimated.
                    "posterior_temp_enabled": self.config.get(
                        "adaptive_routing_posterior_temp_enabled", True
                    ),
                    "posterior_temp_threshold": self.config.get(
                        "adaptive_routing_posterior_temp_threshold", 150
                    ),
                    "posterior_temp_factor": self.config.get(
                        "adaptive_routing_posterior_temp_factor", 3.0
                    ),
                }
            )

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

        logger.info("Routing query %s: %s...", query_id, redact_pii(query)[:100])

        try:
            await self._routing_circuit_breaker.call(lambda: None)

            # Check cache first if enabled. The strategy and constraints are
            # part of the cache identity — the same query routes differently
            # under a different strategy or cost/quality constraints.
            cached_decision = self.cache_manager.check_cache(
                query, context, strategy, constraints
            )
            if cached_decision:
                logger.info(f"Using cached routing for {query_id}")
                await self._routing_circuit_breaker._on_success()
                return cached_decision

            # Step 1: Analyze query complexity
            complexity_analysis = await self.complexity_analyzer.analyze(query, context)

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
            optimization_strategy = self._map_to_optimization_strategy(routing_strategy)

            optimization_result = await self.cost_optimizer.optimize(
                complexity_analysis, optimization_strategy, constraints
            )

            # Step 3: Determine collaboration mode
            collaboration_mode = self._determine_collaboration_mode(
                complexity_analysis, optimization_result
            )

            # Step 3.5: Query episodic memory for routing prior (if enabled)
            episodic_prior = await self._get_episodic_routing_prior(
                complexity_analysis, query
            )

            # Step 3.6: Query adaptive engine for allocation adjustment (if enabled)
            adaptive_recommendation = await self._get_adaptive_allocation_adjustment(
                complexity_analysis, collaboration_mode, episodic_prior
            )

            # Step 4: Allocate agents (with optional memory + adaptive adjustment)
            agent_allocation = self._allocate_agents(
                complexity_analysis,
                collaboration_mode,
                episodic_prior,
                adaptive_recommendation,
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
                fallback_strategy=self._select_fallback_strategy(complexity_analysis),
                monitoring_level=self._select_monitoring_level(complexity_analysis),
                context_requirements=self._determine_context_requirements(
                    complexity_analysis, context
                ),
                memory_allocation=self._allocate_memory(complexity_analysis),
            )

            # Cache decision
            self.cache_manager.cache_decision(
                query, context, decision, strategy, constraints
            )

            # Update metrics
            self.metrics_collector.update_metrics(decision)

            # Store in history for learning
            self.metrics_collector.add_to_history(decision)

            # Trigger adaptive learning if enabled
            if self.learning_enabled:
                task = asyncio.create_task(
                    self.metrics_collector.adapt_from_decision(decision)
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            processing_time = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(
                f"Routing complete for {query_id}: {decision.collaboration_mode.value} "
                f"mode with {decision.agent_allocation.worker_count} agents "
                f"(processing: {processing_time:.1f}ms)"
            )

            await self._routing_circuit_breaker._on_success()
            return decision

        except Exception as e:
            logger.error(f"Routing failed for {query_id}: {e}")
            await self._routing_circuit_breaker._on_failure()
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
            self.enable_adaptive_routing
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

    def _determine_collaboration_mode(
        self,
        complexity_analysis: ComplexityAnalysis,
        optimization_result: OptimizationResult,
    ) -> CollaborationMode:
        """Determine optimal agent collaboration mode."""

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
    ) -> int | None:
        """Query adaptive engine for worker_count adjustment (if enabled and warm).

        Uses a 5-arm bandit (deltas: -2, -1, 0, +1, +2 from memory-adjusted baseline)
        with Thompson Sampling. Reward signal is quality_score from routing_history.

        Args:
            complexity_analysis: Query complexity analysis
            collaboration_mode: Determined collaboration mode
            episodic_prior: Episodic memory prior (may be None)

        Returns:
            Recommended worker_count from adaptive engine, or None if:
            - Flag is OFF
            - History too small (cold start)
            - Engine raises an error (graceful fallback)
        """
        # Guard: flag OFF → no-op (zero overhead)
        if not self.adaptive_routing_enabled or self._adaptive_engine is None:
            return None

        # Guard: cold start → wait for history
        history_size = self.metrics_collector.get_history_size()
        if history_size < self.adaptive_routing_min_history:
            logger.debug(
                f"adaptive_routing: cold start, history {history_size} < {self.adaptive_routing_min_history}"
            )
            return None

        try:
            # Derive experiment_id from collaboration_mode
            experiment_id = f"adaptive_allocation_{collaboration_mode.value}"

            # Register experiment if not already registered (idempotent)
            if experiment_id not in self._adaptive_engine.active_experiments:
                # 5-arm bandit: deltas {-2, -1, 0, +1, +2} from memory-adjusted baseline
                # Arm 0 = -2 workers, Arm 1 = -1, Arm 2 = 0 (no change), Arm 3 = +1, Arm 4 = +2
                variants = ["-2", "-1", "0", "+1", "+2"]
                initial_allocation = dict.fromkeys(variants, 0.2)  # Uniform start

                config = AllocationConfig(
                    strategy=AllocationStrategy.ADAPTIVE_BANDIT,
                    initial_allocation=initial_allocation,
                    min_allocation=0.05,
                    max_allocation=0.70,
                    exploration_rate=0.1,
                    confidence_threshold=0.95,
                    update_frequency_seconds=self.config.get(
                        "adaptive_routing_update_interval_seconds", 300
                    ),
                    enable_guardrails=True,
                    performance_threshold=0.95,
                    safety_sample_size=10,  # Low threshold for faster learning in eval
                )

                await self._adaptive_engine.register_experiment(
                    experiment_id, variants, config
                )

                logger.info(
                    f"adaptive_routing: registered experiment {experiment_id} with 5 arms"
                )

            # Allocate variant (selects arm via Thompson Sampling)
            decision = await self._adaptive_engine.allocate_variant(
                experiment_id,
                user_context={"collaboration_mode": collaboration_mode.value},
            )

            # Map selected variant (delta string) to worker_count adjustment
            delta_map = {"-2": -2, "-1": -1, "0": 0, "+1": 1, "+2": 2}
            selected_delta = delta_map[decision.variant_id]

            # Compute baseline (analytic or memory-adjusted)
            # We don't know the memory-adjusted count here, so we'll return the delta
            # and let _apply_adaptive_adjustment apply it to the baseline
            # But we need to return an absolute count, not a delta
            # So we'll infer the baseline from the collaboration_mode
            baseline = self._infer_baseline_worker_count(
                complexity_analysis, collaboration_mode
            )
            recommended_count = max(1, baseline + selected_delta)

            logger.debug(
                f"adaptive_routing: experiment {experiment_id} selected arm {decision.variant_id} "
                f"(delta {selected_delta}, baseline {baseline} → {recommended_count})"
            )

            return recommended_count

        except Exception as e:
            # Resilient: log and return None (routing proceeds with memory prior only)
            logger.warning(
                f"adaptive_allocation_adjustment failed (graceful fallback): {e}"
            )
            return None

    def _infer_baseline_worker_count(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
    ) -> int:
        """Infer analytic baseline worker_count for a given collaboration_mode.

        This is a heuristic to reconstruct the baseline that _allocate_agents would
        compute, so we can apply bandit deltas on top of it.
        """
        if collaboration_mode == CollaborationMode.DIRECT:
            return 1
        elif collaboration_mode == CollaborationMode.PARALLEL:
            return int(
                min(len(complexity_analysis.domains) + 1, self.max_parallel_workers)
            )
        elif collaboration_mode == CollaborationMode.HIERARCHICAL:
            return int(
                min(complexity_analysis.subtask_count, self.max_agents_per_query)
            )
        elif collaboration_mode == CollaborationMode.DEBATE:
            return 3  # Fixed
        else:  # ENSEMBLE
            return 5

    async def record_routing_outcome(
        self, decision: RoutingDecision, quality_score: float, actual_cost: float
    ) -> None:
        """Record routing outcome for adaptive learning.

        Args:
            decision: The routing decision that was executed
            quality_score: Observed quality score (0.0-1.0)
            actual_cost: Actual cost incurred
        """
        if not self.adaptive_routing_enabled or self._adaptive_engine is None:
            return

        try:
            experiment_id = f"adaptive_allocation_{decision.collaboration_mode.value}"

            # Map actual worker_count back to arm (delta)
            baseline = self._infer_baseline_worker_count(
                decision.complexity_analysis, decision.collaboration_mode
            )
            actual_delta = decision.agent_allocation.worker_count - baseline

            # Clamp delta to {-2, -1, 0, +1, +2} (may be clamped by hard caps)
            clamped_delta = max(-2, min(2, actual_delta))
            delta_to_variant = {-2: "-2", -1: "-1", 0: "0", 1: "+1", 2: "+2"}
            variant_id = delta_to_variant.get(clamped_delta, "0")

            # Compute advantage reward (baseline-relative per collaboration_mode).
            # This isolates the allocation improvement signal from mode-intrinsic
            # quality differences (e.g., DIRECT queries naturally score higher than
            # HIERARCHICAL queries due to inherent complexity, not worker_count).
            mode = decision.collaboration_mode
            if mode not in self._mode_quality_baselines:
                # Initialize with midpoint of typical quality range [0.6, 0.9]
                self._mode_quality_baselines[mode] = 0.75

            quality_baseline = self._mode_quality_baselines[mode]
            advantage = quality_score - quality_baseline

            # Update baseline with exponential moving average (alpha=0.05 for stability)
            self._mode_quality_baselines[mode] = float(
                0.95 * quality_baseline + 0.05 * quality_score
            )

            # Shift advantage to [0, 1] range for Thompson Sampling Beta update
            reward = float(np.clip(advantage + 0.5, 0.0, 1.0))

            await self._adaptive_engine.record_outcome(
                experiment_id, variant_id, reward
            )

            logger.debug(
                f"adaptive_routing: recorded outcome for {experiment_id} "
                f"variant {variant_id} reward {reward:.3f}"
            )

        except Exception as e:
            logger.warning(f"Failed to record adaptive routing outcome: {e}")

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

    def _allocate_agents(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
        episodic_prior: int | None = None,
        adaptive_recommendation: int | None = None,
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

        # Base allocation by collaboration mode
        if collaboration_mode == CollaborationMode.DIRECT:
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
            # Sequential composition: memory first, then adaptive
            memory_adjusted = self._apply_memory_adjustment(
                analytic_count, episodic_prior
            )
            worker_count = self._apply_adaptive_adjustment(
                memory_adjusted, adaptive_recommendation
            )
            worker_count = min(worker_count, self.max_parallel_workers)  # Hard cap
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
            # Sequential composition: memory first, then adaptive
            memory_adjusted = self._apply_memory_adjustment(
                analytic_count, episodic_prior
            )
            worker_count = self._apply_adaptive_adjustment(
                memory_adjusted, adaptive_recommendation
            )
            worker_count = min(worker_count, self.max_agents_per_query)  # Hard cap
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                worker_types=self._get_specialized_worker_types(complexity_analysis),
                max_parallel=min(worker_count, LOW_PARALLELISM),
                timeout_seconds=DEFAULT_AGENT_TIMEOUT,
                retry_attempts=MAX_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.DEBATE:
            # DEBATE is fixed at 3 (no memory adjustment)
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=3,
                worker_types=["analyst", "critic", "synthesizer"],
                max_parallel=LOW_PARALLELISM,
                timeout_seconds=LONG_TIMEOUT,
                retry_attempts=DEFAULT_RETRY_ATTEMPTS,
            )

        else:  # ENSEMBLE
            analytic_count = 5
            # Sequential composition: memory first, then adaptive
            memory_adjusted = self._apply_memory_adjustment(
                analytic_count, episodic_prior
            )
            worker_count = self._apply_adaptive_adjustment(
                memory_adjusted, adaptive_recommendation
            )
            worker_count = max(3, min(worker_count, 7))  # Ensemble: 3-7 range
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

        return health


__all__ = [
    "AgentAllocation",
    "CollaborationMode",
    "MASRouter",
    "RoutingDecision",
    "RoutingMetrics",
    "RoutingStrategy",
]
