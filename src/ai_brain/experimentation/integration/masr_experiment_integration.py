"""
MASR Router Experiment Integration

This module integrates experimentation capabilities directly into the MASR
(Multi-Agent System Router) to enable A/B testing of routing strategies,
collaboration modes, and resource allocation decisions.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.core.constants import (
    DEFAULT_AGENT_TIMEOUT,
    DIRECT_MODE_PARALLELISM,
    EXTENDED_TIMEOUT,
    HIGH_PARALLELISM,
    MAX_PARALLELISM,
    MAX_RETRY_ATTEMPTS,
    MIN_RETRY_ATTEMPTS,
    NO_RETRY,
    SPEED_TEST_TIMEOUT,
)

from ...router.cost_optimizer import OptimizationStrategy
from ...router.masr import (
    AgentAllocation,
    CollaborationMode,
    MASRouter,
    RoutingDecision,
    RoutingStrategy,
)
from ..core.adaptive_allocation_engine import AdaptiveAllocationEngine
from ..core.system_experiment_registry import SystemExperimentRegistry
from ..core.unified_experiment_manager import UnifiedExperimentManager

logger = logging.getLogger(__name__)


class MASRExperimentType(Enum):
    """Types of MASR experiments."""
    
    ROUTING_STRATEGY = "routing_strategy"
    COLLABORATION_MODE = "collaboration_mode"
    AGENT_ALLOCATION = "agent_allocation"
    COMPLEXITY_THRESHOLD = "complexity_threshold"
    FALLBACK_STRATEGY = "fallback_strategy"
    COST_OPTIMIZATION = "cost_optimization"


@dataclass
class MASRExperimentConfig:
    """Configuration for MASR experiments."""
    
    experiment_type: MASRExperimentType
    variants: dict[str, dict[str, Any]]
    metrics_to_track: list[str] = field(default_factory=lambda: [
        "routing_latency_ms",
        "total_cost",
        "response_quality",
        "success_rate",
        "fallback_triggered"
    ])
    allocation_strategy: str = "epsilon_greedy"
    epsilon: float = 0.1
    min_samples_per_variant: int = 100


@dataclass
class MASRExperimentResult:
    """Result from MASR experiment."""
    
    query_id: str
    variant_id: str
    routing_decision: RoutingDecision
    execution_time_ms: float
    cost: float
    quality_score: float
    error_occurred: bool = False
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class MASRExperimentalRouter(MASRouter):
    """
    Extended MASR Router with integrated experimentation capabilities.
    
    This class wraps the existing MASRouter to add A/B testing functionality
    for routing decisions, collaboration modes, and optimization strategies.
    """
    
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize experimental router."""
        super().__init__(*args, **kwargs)
        
        # Experimentation components
        self.experiment_manager = UnifiedExperimentManager()
        self.allocation_engine = AdaptiveAllocationEngine()
        self.registry = SystemExperimentRegistry()
        
        # Active experiments
        self.active_experiments: dict[str, MASRExperimentConfig] = {}
        
        # Experiment results buffer
        self.results_buffer: list[MASRExperimentResult] = []
        
    async def initialize_experiments(self) -> None:
        """Initialize default MASR experiments."""
        # Routing strategy experiment
        routing_experiment = MASRExperimentConfig(
            experiment_type=MASRExperimentType.ROUTING_STRATEGY,
            variants={
                "control": {"strategy": RoutingStrategy.BALANCED},
                "cost_efficient": {"strategy": RoutingStrategy.COST_EFFICIENT},
                "quality_focused": {"strategy": RoutingStrategy.QUALITY_FOCUSED},
                "adaptive": {"strategy": RoutingStrategy.ADAPTIVE}
            }
        )
        
        # Collaboration mode experiment
        collab_experiment = MASRExperimentConfig(
            experiment_type=MASRExperimentType.COLLABORATION_MODE,
            variants={
                "hierarchical": {"mode": CollaborationMode.HIERARCHICAL},
                "parallel": {"mode": CollaborationMode.PARALLEL},
                "ensemble": {"mode": CollaborationMode.ENSEMBLE},
                "debate": {"mode": CollaborationMode.DEBATE}
            }
        )
        
        # Register experiments
        await self.register_experiment("routing_strategy_test", routing_experiment)
        await self.register_experiment("collaboration_mode_test", collab_experiment)
        
        logger.info("MASR experiments initialized")
    
    async def register_experiment(self, 
                                 experiment_id: str,
                                 config: MASRExperimentConfig) -> bool:
        """
        Register a new MASR experiment.
        
        Args:
            experiment_id: Unique experiment identifier
            config: Experiment configuration
            
        Returns:
            Success status
        """
        try:
            # Register with experiment manager
            _experiment_spec = {
                "id": experiment_id,
                "type": config.experiment_type.value,
                "variants": config.variants,
                "metrics": config.metrics_to_track,
                "allocation": {
                    "strategy": config.allocation_strategy,
                    "epsilon": config.epsilon
                },
                "status": "active",
                "created_at": datetime.now()
            }
            
            experiment_config_dict: dict[str, Any] = {
                "name": experiment_id,
                "description": f"MASR {config.experiment_type.value} experiment",
                "type": "ab_test",
                "components": ["masr_routing"],
                "variants": [
                    {
                        "name": variant_name,
                        "allocation": 1.0 / len(config.variants),
                        "configuration": variant_config
                    }
                    for variant_name, variant_config in config.variants.items()
                ],
                "metrics": {
                    "primary": config.metrics_to_track,
                    "secondary": [],
                    "guardrail": []
                }
            }
            await self.experiment_manager.create_experiment(experiment_config_dict)
            
            # Store locally
            self.active_experiments[experiment_id] = config
            
            # Register with system registry
            from ..core.unified_experiment_manager import (
                ExperimentMetrics,
                ExperimentStatus,
                ExperimentType,
                ExperimentVariant,
                StatisticalConfig,
                SystemComponent,
                SystemExperiment,
            )

            # Create proper ExperimentVariant objects
            variants_list = [
                ExperimentVariant(
                    id=variant["name"],
                    name=variant["name"],
                    description=f"Variant {variant['name']}",
                    allocation=variant["allocation"],
                    configuration=variant["configuration"]
                )
                for variant in experiment_config_dict["variants"]
            ]

            # Create proper ExperimentMetrics object
            metrics_obj = ExperimentMetrics(
                primary_metrics=experiment_config_dict["metrics"]["primary"],
                secondary_metrics=experiment_config_dict["metrics"]["secondary"],
                guardrail_metrics=experiment_config_dict["metrics"]["guardrail"]
            )

            # Create proper StatisticalConfig object
            statistical_config_obj = StatisticalConfig()

            sys_exp = SystemExperiment(
                id=experiment_id,
                name=experiment_id,
                description=f"MASR {config.experiment_type.value} experiment",
                type=ExperimentType.AB_TEST,
                components=[SystemComponent.MASR_ROUTING],
                variants=variants_list,
                metrics=metrics_obj,
                statistical_config=statistical_config_obj,
                success_criteria=[],
                status=ExperimentStatus.CREATED
            )
            await self.registry.register_experiment(sys_exp)
            
            logger.info(f"Registered MASR experiment: {experiment_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to register experiment {experiment_id}: {e}")
            return False
    
    async def route_with_experiment(self,
                                   query: str,
                                   context: dict[str, Any] | None = None) -> RoutingDecision:
        """
        Route query with experimental variant selection.
        
        This method wraps the original route() method to add experimentation.
        
        Args:
            query: The query to route
            context: Optional context for routing
            
        Returns:
            RoutingDecision with experimental modifications
        """
        start_time = datetime.now()
        context = context or {}
        
        # Analyze query complexity first
        complexity_analysis = await self.complexity_analyzer.analyze(query, context)
        
        # Check for active routing strategy experiment
        routing_decision = None
        variant_used = "control"
        experiment_metadata = {}
        
        if "routing_strategy_test" in self.active_experiments:
            # Get variant allocation
            allocation = await self.allocation_engine.allocate_variant(
                experiment_id="routing_strategy_test",
                user_context={
                    "complexity": complexity_analysis.level.value,
                    "query_length": len(query),
                    "has_context": bool(context)
                }
            )
            
            variant_used = allocation.variant_id
            config = self.active_experiments["routing_strategy_test"]
            
            if variant_used in config.variants:
                # Apply experimental routing strategy
                strategy = config.variants[variant_used]["strategy"]
                routing_decision = await self._route_with_strategy(
                    query, context, complexity_analysis, strategy
                )
                experiment_metadata["routing_strategy"] = strategy.value
        
        # Check for collaboration mode experiment
        if "collaboration_mode_test" in self.active_experiments and routing_decision:
            allocation = await self.allocation_engine.allocate_variant(
                experiment_id="collaboration_mode_test",
                user_context={
                    "complexity": complexity_analysis.level.value,
                    "estimated_cost": routing_decision.estimated_cost
                }
            )
            
            collab_variant = allocation.variant_id
            config = self.active_experiments["collaboration_mode_test"]
            
            if collab_variant in config.variants:
                # Override collaboration mode
                mode = config.variants[collab_variant]["mode"]
                routing_decision.collaboration_mode = mode
                experiment_metadata["collaboration_mode"] = mode.value
        
        # Fall back to standard routing if no experimental decision
        if not routing_decision:
            routing_decision = await self.route(query, context)
        
        # Track experiment result
        execution_time = (datetime.now() - start_time).total_seconds() * 1000
        
        result = MASRExperimentResult(
            query_id=routing_decision.query_id,
            variant_id=variant_used,
            routing_decision=routing_decision,
            execution_time_ms=execution_time,
            cost=routing_decision.estimated_cost,
            quality_score=routing_decision.estimated_quality,
            metadata=experiment_metadata
        )
        
        # Store result
        self.results_buffer.append(result)
        
        # Report metrics to experiment manager
        await self._report_metrics(result)
        
        return routing_decision
    
    async def _route_with_strategy(self,
                                  query: str,
                                  context: dict[str, Any],
                                  complexity_analysis: Any,
                                  strategy: RoutingStrategy) -> RoutingDecision:
        """
        Route with specific experimental strategy.
        
        Args:
            query: Query to route
            context: Routing context
            complexity_analysis: Query complexity analysis
            strategy: Routing strategy to use
            
        Returns:
            RoutingDecision based on strategy
        """
        # Set the routing strategy
        original_strategy = getattr(self, '_default_strategy', RoutingStrategy.BALANCED)
        
        try:
            # Temporarily override strategy
            self._default_strategy = strategy
            
            # Perform routing with experimental strategy
            if strategy == RoutingStrategy.COST_EFFICIENT:
                return await self._cost_efficient_routing(
                    query, context, complexity_analysis
                )
            elif strategy == RoutingStrategy.QUALITY_FOCUSED:
                return await self._quality_focused_routing(
                    query, context, complexity_analysis
                )
            elif strategy == RoutingStrategy.SPEED_FIRST:
                return await self._speed_first_routing(
                    query, context, complexity_analysis
                )
            elif strategy == RoutingStrategy.ADAPTIVE:
                return await self._adaptive_routing(
                    query, context, complexity_analysis
                )
            else:
                # Default balanced routing
                return await self.route(query, context)
                
        finally:
            # Restore original strategy
            self._default_strategy = original_strategy
    
    async def _cost_efficient_routing(self,
                                    query: str,
                                    context: dict[str, Any],
                                    complexity_analysis: Any) -> RoutingDecision:
        """Cost-optimized routing strategy."""
        # Prefer cheaper models and minimal agent allocation
        allocation = AgentAllocation(
            supervisor_type="cost_efficient_supervisor",
            worker_count=1,  # Minimal workers
            worker_types=["basic_worker"],
            max_parallel=DIRECT_MODE_PARALLELISM,
            timeout_seconds=EXTENDED_TIMEOUT,  # Longer timeout for cheaper models
            retry_attempts=MIN_RETRY_ATTEMPTS
        )
        
        return RoutingDecision(
            query_id=f"exp_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            complexity_analysis=complexity_analysis,
            optimization_result=await self.cost_optimizer.optimize(
                complexity_analysis,
                OptimizationStrategy.COST_MINIMIZED
            ),
            collaboration_mode=CollaborationMode.DIRECT,
            agent_allocation=allocation,
            estimated_cost=0.001,  # Minimal cost
            estimated_latency_ms=2000,
            estimated_quality=0.7,
            confidence_score=0.8,
            fallback_strategy="basic_fallback"
        )
    
    async def _quality_focused_routing(self,
                                      query: str,
                                      context: dict[str, Any],
                                      complexity_analysis: Any) -> RoutingDecision:
        """Quality-optimized routing strategy."""
        # Use best models and comprehensive agent teams
        allocation = AgentAllocation(
            supervisor_type="expert_supervisor",
            worker_count=5,  # More workers for quality
            worker_types=["expert_worker", "specialist_worker"],
            max_parallel=HIGH_PARALLELISM,
            timeout_seconds=DEFAULT_AGENT_TIMEOUT,
            retry_attempts=MAX_RETRY_ATTEMPTS
        )
        
        return RoutingDecision(
            query_id=f"exp_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            complexity_analysis=complexity_analysis,
            optimization_result=await self.cost_optimizer.optimize(
                complexity_analysis,
                OptimizationStrategy.PERFORMANCE_OPTIMIZED
            ),
            collaboration_mode=CollaborationMode.ENSEMBLE,
            agent_allocation=allocation,
            estimated_cost=0.05,
            estimated_latency_ms=5000,
            estimated_quality=0.95,
            confidence_score=0.95,
            fallback_strategy="comprehensive_fallback"
        )
    
    async def _speed_first_routing(self,
                                  query: str,
                                  context: dict[str, Any],
                                  complexity_analysis: Any) -> RoutingDecision:
        """Speed-optimized routing strategy."""
        # Minimize latency with fast models and parallel processing
        allocation = AgentAllocation(
            supervisor_type="fast_supervisor",
            worker_count=3,
            worker_types=["fast_worker"],
            max_parallel=MAX_PARALLELISM,  # High parallelism
            timeout_seconds=SPEED_TEST_TIMEOUT,  # Short timeout
            retry_attempts=NO_RETRY  # No retries for speed
        )
        
        return RoutingDecision(
            query_id=f"exp_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            complexity_analysis=complexity_analysis,
            optimization_result=await self.cost_optimizer.optimize(
                complexity_analysis,
                OptimizationStrategy.LATENCY_OPTIMIZED
            ),
            collaboration_mode=CollaborationMode.PARALLEL,
            agent_allocation=allocation,
            estimated_cost=0.01,
            estimated_latency_ms=500,
            estimated_quality=0.8,
            confidence_score=0.85,
            fallback_strategy="fast_fallback"
        )
    
    async def _adaptive_routing(self,
                               query: str,
                               context: dict[str, Any],
                               complexity_analysis: Any) -> RoutingDecision:
        """Adaptive routing based on historical performance."""
        # Analyze recent performance metrics
        recent_metrics = await self._get_recent_performance_metrics()
        
        # Choose strategy based on performance
        if recent_metrics.get("error_rate", 0) > 0.1:
            # High error rate - focus on quality
            return await self._quality_focused_routing(query, context, complexity_analysis)
        elif recent_metrics.get("avg_latency_ms", 0) > 3000:
            # High latency - focus on speed
            return await self._speed_first_routing(query, context, complexity_analysis)
        elif recent_metrics.get("avg_cost", 0) > 0.02:
            # High cost - focus on efficiency
            return await self._cost_efficient_routing(query, context, complexity_analysis)
        else:
            # Balanced approach
            return await self.route(query, context)
    
    async def _report_metrics(self, result: MASRExperimentResult) -> None:
        """Report experiment metrics to the experiment manager."""
        metrics = {
            "routing_latency_ms": result.execution_time_ms,
            "total_cost": result.cost,
            "response_quality": result.quality_score,
            "success_rate": 1.0 if not result.error_occurred else 0.0,
            "fallback_triggered": 1.0 if result.fallback_used else 0.0
        }

        # Report to experiment manager
        for experiment_id in self.active_experiments:
            for metric_name, metric_value in metrics.items():
                await self.experiment_manager.track_metric(
                    experiment_id=experiment_id,
                    variant_id=result.variant_id,
                    metric_name=metric_name,
                    value=metric_value,
                    context={
                        "query_id": result.query_id,
                        "timestamp": datetime.now().isoformat()
                    }
                )
    
    async def _get_recent_performance_metrics(self) -> dict[str, float]:
        """Get recent performance metrics for adaptive routing."""
        if not self.results_buffer:
            return {}
        
        # Analyze last 100 results
        recent = self.results_buffer[-100:]
        
        metrics = {
            "error_rate": sum(1 for r in recent if r.error_occurred) / len(recent),
            "avg_latency_ms": sum(r.execution_time_ms for r in recent) / len(recent),
            "avg_cost": sum(r.cost for r in recent) / len(recent),
            "avg_quality": sum(r.quality_score for r in recent) / len(recent),
            "fallback_rate": sum(1 for r in recent if r.fallback_used) / len(recent)
        }
        
        return metrics
    
    async def get_experiment_results(self, 
                                    experiment_id: str) -> dict[str, Any]:
        """
        Get results for a specific experiment.
        
        Args:
            experiment_id: Experiment to get results for
            
        Returns:
            Dictionary with experiment results and statistics
        """
        if experiment_id not in self.active_experiments:
            return {"error": f"Experiment {experiment_id} not found"}

        # Get experiment from manager
        experiment = await self.experiment_manager.get_experiment(experiment_id)
        results: dict[str, Any] = {"experiment": experiment.__dict__ if experiment else {}}

        # Add MASR-specific analysis
        config = self.active_experiments[experiment_id]
        variant_results: dict[str, Any] = {}
        
        for variant_id in config.variants:
            variant_data = [r for r in self.results_buffer 
                          if r.variant_id == variant_id]
            
            if variant_data:
                variant_results[variant_id] = {
                    "sample_size": len(variant_data),
                    "avg_latency_ms": sum(r.execution_time_ms for r in variant_data) / len(variant_data),
                    "avg_cost": sum(r.cost for r in variant_data) / len(variant_data),
                    "avg_quality": sum(r.quality_score for r in variant_data) / len(variant_data),
                    "error_rate": sum(1 for r in variant_data if r.error_occurred) / len(variant_data),
                    "fallback_rate": sum(1 for r in variant_data if r.fallback_used) / len(variant_data)
                }
        
        results["variant_analysis"] = variant_results
        results["experiment_type"] = config.experiment_type.value
        
        return results
    
    async def stop_experiment(self, experiment_id: str) -> bool:
        """
        Stop an active experiment.
        
        Args:
            experiment_id: Experiment to stop
            
        Returns:
            Success status
        """
        if experiment_id not in self.active_experiments:
            return False
        
        try:
            # Stop in experiment manager
            await self.experiment_manager.stop_experiment(experiment_id, reason="manual_stop")

            # Stop in registry
            await self.registry.stop_experiment(experiment_id, reason="manual")

            # Remove from active experiments
            del self.active_experiments[experiment_id]

            logger.info(f"Stopped MASR experiment: {experiment_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop experiment {experiment_id}: {e}")
            return False
    
    async def optimize_routing_parameters(self,
                                         optimization_goals: dict[str, float]) -> dict[str, Any]:
        """
        Optimize routing parameters based on experiment results.
        
        Args:
            optimization_goals: Weights for different metrics
                               (e.g., {"cost": 0.3, "quality": 0.5, "speed": 0.2})
        
        Returns:
            Optimized parameter configuration
        """
        # Analyze all experiment results
        all_results = {}
        for exp_id in self.active_experiments:
            all_results[exp_id] = await self.get_experiment_results(exp_id)
        
        # Find best performing variants
        best_config = {
            "routing_strategy": RoutingStrategy.BALANCED,
            "collaboration_mode": CollaborationMode.HIERARCHICAL,
            "worker_count": 3,
            "timeout_seconds": 300
        }
        
        best_score = -float('inf')
        
        for exp_id, results in all_results.items():
            if "variant_analysis" not in results:
                continue
            
            for variant_id, metrics in results["variant_analysis"].items():
                # Calculate weighted score
                score = 0.0
                if "cost" in optimization_goals:
                    score -= metrics.get("avg_cost", 0) * optimization_goals["cost"]
                if "quality" in optimization_goals:
                    score += metrics.get("avg_quality", 0) * optimization_goals["quality"]
                if "speed" in optimization_goals:
                    score -= metrics.get("avg_latency_ms", 0) / 1000 * optimization_goals["speed"]
                
                if score > best_score:
                    best_score = score
                    
                    # Update best config based on variant
                    config = self.active_experiments[exp_id]
                    if variant_id in config.variants:
                        variant_config = config.variants[variant_id]
                        
                        if "strategy" in variant_config:
                            best_config["routing_strategy"] = variant_config["strategy"]
                        if "mode" in variant_config:
                            best_config["collaboration_mode"] = variant_config["mode"]
        
        return {
            "optimized_config": best_config,
            "expected_score": best_score,
            "based_on_experiments": list(all_results.keys())
        }