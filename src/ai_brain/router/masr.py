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
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from src.core.types import HealthCheckDict

if TYPE_CHECKING:
    from src.ai_brain.config.model_config_manager import ModelConfigManager

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

logger = logging.getLogger(__name__)


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
        self.cost_optimizer = CostOptimizer(
            cost_opt_config, model_config_manager
        )

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
            min_history_for_adaptation=self.config.get("min_history_for_adaptation", 100),
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

        logger.info(f"Routing query {query_id}: {query[:100]}...")

        try:
            await self._routing_circuit_breaker.call(lambda: None)

            # Check cache first if enabled
            cached_decision = self.cache_manager.check_cache(query, context)
            if cached_decision:
                logger.info(f"Using cached routing for {query_id}")
                await self._routing_circuit_breaker._on_success()
                return cast(RoutingDecision, cached_decision)

            # Step 1: Analyze query complexity
            complexity_analysis = await self.complexity_analyzer.analyze(query, context)

            # Step 2: Optimize model selection
            routing_strategy = strategy or self._select_routing_strategy(
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

            # Step 4: Allocate agents
            agent_allocation = self._allocate_agents(
                complexity_analysis, collaboration_mode
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
            self.cache_manager.cache_decision(query, context, decision)

            # Update metrics
            self.metrics_collector.update_metrics(decision)

            # Store in history for learning
            self.metrics_collector.add_to_history(decision)

            # Trigger adaptive learning if enabled
            if self.learning_enabled:
                task = asyncio.create_task(self.metrics_collector.adapt_from_decision(decision))
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
        if self.enable_adaptive_routing and self.metrics_collector.get_history_size() > 100:
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

    def _allocate_agents(
        self,
        complexity_analysis: ComplexityAnalysis,
        collaboration_mode: CollaborationMode,
    ) -> AgentAllocation:
        """Determine optimal agent allocation with supervisor-based hierarchical routing."""

        # Get supervisor types based on domains
        supervisor_types = self._get_domain_supervisor_types(complexity_analysis.domains)
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
            worker_count = min(
                len(complexity_analysis.domains) + 1, self.max_parallel_workers
            )
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                worker_types=self._get_domain_worker_types(complexity_analysis.domains),
                max_parallel=worker_count,
                timeout_seconds=MEDIUM_TIMEOUT,
                retry_attempts=DEFAULT_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.HIERARCHICAL:
            worker_count = min(
                complexity_analysis.subtask_count, self.max_agents_per_query
            )
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=worker_count,
                worker_types=self._get_specialized_worker_types(complexity_analysis),
                max_parallel=min(worker_count, LOW_PARALLELISM),
                timeout_seconds=DEFAULT_AGENT_TIMEOUT,
                retry_attempts=MAX_RETRY_ATTEMPTS,
            )

        elif collaboration_mode == CollaborationMode.DEBATE:
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=3,  # Typical debate size
                worker_types=["analyst", "critic", "synthesizer"],
                max_parallel=LOW_PARALLELISM,
                timeout_seconds=LONG_TIMEOUT,
                retry_attempts=DEFAULT_RETRY_ATTEMPTS,
            )

        else:  # ENSEMBLE
            return AgentAllocation(
                supervisor_type=primary_supervisor,
                worker_count=5,
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
            "service": "service",
            "multimodal": "content",  # Fallback to content supervisor for multimodal
        }

        for domain in domains:
            domain_name = domain.value if hasattr(domain, "value") else str(domain)
            supervisor_type = domain_supervisors.get(domain_name, "research")  # Default to research
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
        if complexity_analysis.level == ComplexityLevel.COMPLEX or complexity_analysis.uncertainty > 0.7:
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
