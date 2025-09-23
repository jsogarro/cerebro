"""
MASR Router Experiment Integration

This module integrates experimentation capabilities directly into the MASR
(Multi-Agent System Router) to enable A/B testing of routing strategies,
collaboration modes, and resource allocation decisions.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ...router.masr import (
    MASRouter, 
    RoutingStrategy, 
    CollaborationMode,
    RoutingDecision,
    AgentAllocation
)
from ...router.query_analyzer import ComplexityLevel
from ..core.unified_experiment_manager import UnifiedExperimentManager
from ..core.adaptive_allocation_engine import AdaptiveAllocationEngine
from ..core.system_experiment_registry import SystemExperimentRegistry

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
    variants: Dict[str, Dict[str, Any]]
    metrics_to_track: List[str] = field(default_factory=lambda: [
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
    metadata: Dict[str, Any] = field(default_factory=dict)


class MASRExperimentalRouter(MASRouter):
    """
    Extended MASR Router with integrated experimentation capabilities.
    
    This class wraps the existing MASRouter to add A/B testing functionality
    for routing decisions, collaboration modes, and optimization strategies.
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize experimental router."""
        super().__init__(*args, **kwargs)
        
        # Experimentation components
        self.experiment_manager = UnifiedExperimentManager()
        self.allocation_engine = AdaptiveAllocationEngine()
        self.registry = SystemExperimentRegistry()
        
        # Active experiments
        self.active_experiments: Dict[str, MASRExperimentConfig] = {}
        
        # Experiment results buffer
        self.results_buffer: List[MASRExperimentResult] = []
        
    async def initialize_experiments(self):
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
            experiment_spec = {
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
            
            await self.experiment_manager.create_experiment(
                experiment_id=experiment_id,
                experiment_type="masr_routing",
                variants=list(config.variants.keys()),
                allocation_strategy=config.allocation_strategy,
                metrics=config.metrics_to_track
            )
            
            # Store locally
            self.active_experiments[experiment_id] = config
            
            # Register with system registry
            await self.registry.register_experiment(
                experiment_id=experiment_id,
                component="masr_router",
                experiment_spec=experiment_spec
            )
            
            logger.info(f"Registered MASR experiment: {experiment_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to register experiment {experiment_id}: {e}")
            return False
    
    async def route_with_experiment(self,
                                   query: str,
                                   context: Optional[Dict[str, Any]] = None) -> RoutingDecision:
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
        complexity_analysis = await self.query_analyzer.analyze(query, context)
        
        # Check for active routing strategy experiment
        routing_decision = None
        variant_used = "control"
        experiment_metadata = {}
        
        if "routing_strategy_test" in self.active_experiments:
            # Get variant allocation
            allocation = await self.allocation_engine.allocate_variant(
                experiment_id="routing_strategy_test",
                user_context={
                    "complexity": complexity_analysis.complexity_level.value,
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
                    "complexity": complexity_analysis.complexity_level.value,
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
                                  context: Dict[str, Any],
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
                                    context: Dict[str, Any],
                                    complexity_analysis: Any) -> RoutingDecision:
        """Cost-optimized routing strategy."""
        # Prefer cheaper models and minimal agent allocation
        allocation = AgentAllocation(
            supervisor_type="cost_efficient_supervisor",
            worker_count=1,  # Minimal workers
            worker_types=["basic_worker"],
            max_parallel=1,
            timeout_seconds=600,  # Longer timeout for cheaper models
            retry_attempts=1
        )
        
        return RoutingDecision(
            query_id=f"exp_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            complexity_analysis=complexity_analysis,
            optimization_result=await self.cost_optimizer.optimize(
                complexity_analysis,
                {"prefer_cost": True}
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
                                      context: Dict[str, Any],
                                      complexity_analysis: Any) -> RoutingDecision:
        """Quality-optimized routing strategy."""
        # Use best models and comprehensive agent teams
        allocation = AgentAllocation(
            supervisor_type="expert_supervisor",
            worker_count=5,  # More workers for quality
            worker_types=["expert_worker", "specialist_worker"],
            max_parallel=5,
            timeout_seconds=300,
            retry_attempts=3
        )
        
        return RoutingDecision(
            query_id=f"exp_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            complexity_analysis=complexity_analysis,
            optimization_result=await self.cost_optimizer.optimize(
                complexity_analysis,
                {"prefer_quality": True}
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
                                  context: Dict[str, Any],
                                  complexity_analysis: Any) -> RoutingDecision:
        """Speed-optimized routing strategy."""
        # Minimize latency with fast models and parallel processing
        allocation = AgentAllocation(
            supervisor_type="fast_supervisor",
            worker_count=3,
            worker_types=["fast_worker"],
            max_parallel=10,  # High parallelism
            timeout_seconds=30,  # Short timeout
            retry_attempts=0  # No retries for speed
        )
        
        return RoutingDecision(
            query_id=f"exp_{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            complexity_analysis=complexity_analysis,
            optimization_result=await self.cost_optimizer.optimize(
                complexity_analysis,
                {"prefer_speed": True}
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
                               context: Dict[str, Any],
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
    
    async def _report_metrics(self, result: MASRExperimentResult):
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
            await self.experiment_manager.record_metrics(
                experiment_id=experiment_id,
                variant_id=result.variant_id,
                metrics=metrics,
                context={
                    "query_id": result.query_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
    
    async def _get_recent_performance_metrics(self) -> Dict[str, float]:
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
                                    experiment_id: str) -> Dict[str, Any]:
        """
        Get results for a specific experiment.
        
        Args:
            experiment_id: Experiment to get results for
            
        Returns:
            Dictionary with experiment results and statistics
        """
        if experiment_id not in self.active_experiments:
            return {"error": f"Experiment {experiment_id} not found"}
        
        # Get results from experiment manager
        results = await self.experiment_manager.get_experiment_results(experiment_id)
        
        # Add MASR-specific analysis
        config = self.active_experiments[experiment_id]
        variant_results = {}
        
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
            await self.experiment_manager.stop_experiment(experiment_id)
            
            # Remove from active experiments
            del self.active_experiments[experiment_id]
            
            # Update registry
            await self.registry.update_experiment_status(
                experiment_id=experiment_id,
                status="stopped"
            )
            
            logger.info(f"Stopped MASR experiment: {experiment_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop experiment {experiment_id}: {e}")
            return False
    
    async def optimize_routing_parameters(self,
                                         optimization_goals: Dict[str, float]) -> Dict[str, Any]:
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