"""
Hierarchical Cost Model

Extended cost model for hierarchical supervisor/worker systems that accounts for:
- Supervisor instantiation and coordination overhead
- Worker coordination and communication costs
- TalkHier multi-round refinement costs
- Cross-supervisor collaboration overhead
- Resource pooling and caching benefits

This module extends the existing CostOptimizer to provide accurate cost
predictions for hierarchical multi-agent systems.
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from structlog import get_logger

from ...agents.supervisors.base_supervisor import SupervisionMode
from .cost_optimizer import CostOptimizer
from .masr import RoutingStrategy
from .query_analyzer import ComplexityAnalysis, ComplexityLevel

logger = get_logger()


class ResourceType(Enum):
    """Types of resources in hierarchical systems."""
    
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    COORDINATION = "coordination"
    COMMUNICATION = "communication"
    VALIDATION = "validation"
    CACHING = "caching"
    STORAGE = "storage"


@dataclass
class HierarchicalCostFactors:
    """Cost factors specific to hierarchical systems."""
    
    # Base cost factors (per unit)
    supervisor_instantiation_cost: float = 0.002  # Cost to start a supervisor
    worker_instantiation_cost: float = 0.001     # Cost to start a worker
    coordination_overhead_cost: float = 0.0005   # Cost per coordination event
    
    # Communication costs
    message_cost_per_token: float = 0.00001      # Cost per token in messages
    refinement_round_multiplier: float = 1.5     # Cost multiplier per refinement round
    consensus_building_cost: float = 0.0008      # Cost for consensus evaluation
    
    # Time-based costs (per second)
    supervisor_runtime_cost_per_sec: float = 0.0001
    worker_runtime_cost_per_sec: float = 0.00005
    
    # Efficiency factors (cost reductions)
    resource_pooling_efficiency: float = 0.15    # 15% cost reduction from pooling
    caching_efficiency: float = 0.10             # 10% cost reduction from caching
    parallel_efficiency: float = 0.08            # 8% cost reduction from parallelization
    
    # Scaling factors
    worker_coordination_scaling: float = 0.2     # Cost scales with worker count
    complexity_scaling_factor: float = 0.3      # Cost scales with complexity


@dataclass
class SupervisorCostProfile:
    """Cost profile for a supervisor type."""
    
    supervisor_type: str
    base_instantiation_cost: float
    average_runtime_seconds: float
    worker_coordination_overhead: float
    
    # Performance characteristics affecting cost
    reliability_score: float = 0.95  # Higher reliability reduces retry costs
    efficiency_score: float = 0.85   # Higher efficiency reduces resource usage
    
    # Resource requirements
    memory_cost_per_mb: float = 0.000001
    storage_cost_per_mb: float = 0.0000001
    
    # Quality-cost tradeoffs
    quality_premium: float = 0.0     # Additional cost for higher quality


@dataclass
class HierarchicalCostEstimate:
    """Comprehensive cost estimate for hierarchical execution."""
    
    # Base model costs (from existing CostOptimizer)
    base_model_cost: float = 0.0
    estimated_tokens: int = 0
    
    # Hierarchical system costs
    supervisor_costs: dict[str, float] = field(default_factory=dict)
    worker_costs: dict[str, float] = field(default_factory=dict)
    coordination_costs: dict[str, float] = field(default_factory=dict)
    communication_costs: dict[str, float] = field(default_factory=dict)
    
    # Efficiency savings
    efficiency_savings: dict[str, float] = field(default_factory=dict)
    
    # Total cost breakdown
    total_instantiation_cost: float = 0.0
    total_runtime_cost: float = 0.0
    total_communication_cost: float = 0.0
    total_coordination_cost: float = 0.0
    total_efficiency_savings: float = 0.0
    
    # Final totals
    gross_cost: float = 0.0
    net_cost: float = 0.0
    
    # Quality and performance predictions
    predicted_quality_score: float = 0.85
    predicted_execution_time_seconds: float = 300.0
    confidence: float = 0.8
    
    # Cost accuracy metrics
    cost_variance: float = 0.0  # Expected variance in cost
    
    def calculate_totals(self) -> None:
        """Calculate total costs from components."""
        
        # Sum component costs
        self.total_instantiation_cost = (
            sum(self.supervisor_costs.values()) + 
            sum(self.worker_costs.values())
        )
        
        self.total_coordination_cost = sum(self.coordination_costs.values())
        self.total_communication_cost = sum(self.communication_costs.values())
        self.total_efficiency_savings = sum(self.efficiency_savings.values())
        
        # Calculate gross cost
        self.gross_cost = (
            self.base_model_cost +
            self.total_instantiation_cost +
            self.total_runtime_cost +
            self.total_communication_cost +
            self.total_coordination_cost
        )
        
        # Apply efficiency savings
        self.net_cost = self.gross_cost - self.total_efficiency_savings


class SupervisorCostCalculator:
    """Calculates costs specific to supervisor instantiation and coordination."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize supervisor cost calculator."""
        self.config = config or {}
        
        # Initialize supervisor cost profiles
        self.supervisor_profiles: dict[str, SupervisorCostProfile] = {}
        self._initialize_supervisor_profiles()
        
        # Cost factors
        self.cost_factors = HierarchicalCostFactors()
        
        # Update cost factors from config
        if self.config:
            for field in ['supervisor_instantiation_cost', 'worker_instantiation_cost', 
                         'coordination_overhead_cost', 'message_cost_per_token']:
                if field in self.config:
                    setattr(self.cost_factors, field, self.config[field])
    
    def _initialize_supervisor_profiles(self) -> None:
        """Initialize built-in supervisor cost profiles."""
        
        # Research Supervisor Profile
        self.supervisor_profiles["research"] = SupervisorCostProfile(
            supervisor_type="research",
            base_instantiation_cost=0.003,      # Higher cost for comprehensive setup
            average_runtime_seconds=90.0,       # 1.5 minutes average
            worker_coordination_overhead=0.8,   # Moderate coordination overhead
            reliability_score=0.95,
            efficiency_score=0.88,
            quality_premium=0.001,              # Small premium for higher quality
        )
        
        # Content Supervisor Profile (placeholder - would be implemented)
        self.supervisor_profiles["content"] = SupervisorCostProfile(
            supervisor_type="content",
            base_instantiation_cost=0.002,
            average_runtime_seconds=60.0,
            worker_coordination_overhead=0.6,
            reliability_score=0.92,
            efficiency_score=0.85,
        )
        
        # Analytics Supervisor Profile (placeholder - would be implemented)
        self.supervisor_profiles["analytics"] = SupervisorCostProfile(
            supervisor_type="analytics", 
            base_instantiation_cost=0.0025,
            average_runtime_seconds=120.0,
            worker_coordination_overhead=0.9,   # High coordination for data tasks
            reliability_score=0.90,
            efficiency_score=0.90,
        )
    
    def calculate_supervisor_instantiation_cost(
        self, 
        supervisor_type: str,
        worker_count: int,
        complexity_level: ComplexityLevel
    ) -> float:
        """Calculate cost of supervisor instantiation."""
        
        profile = self.supervisor_profiles.get(supervisor_type)
        if not profile:
            # Use default profile
            profile = SupervisorCostProfile(
                supervisor_type="default",
                base_instantiation_cost=self.cost_factors.supervisor_instantiation_cost,
                average_runtime_seconds=60.0,
                worker_coordination_overhead=0.7
            )
        
        # Base instantiation cost
        base_cost = profile.base_instantiation_cost
        
        # Scale by worker count (more workers = more setup cost)
        worker_scaling = 1.0 + (worker_count - 1) * 0.1
        
        # Scale by complexity
        complexity_scaling = {
            ComplexityLevel.SIMPLE: 0.8,
            ComplexityLevel.MODERATE: 1.0,
            ComplexityLevel.COMPLEX: 1.3,
        }.get(complexity_level, 1.0)
        
        instantiation_cost = base_cost * worker_scaling * complexity_scaling
        
        return instantiation_cost
    
    def calculate_supervisor_runtime_cost(
        self,
        supervisor_type: str,
        estimated_runtime_seconds: float,
        refinement_rounds: int = 1
    ) -> float:
        """Calculate ongoing runtime cost for supervisor."""
        
        profile = self.supervisor_profiles.get(supervisor_type)
        if not profile:
            profile = SupervisorCostProfile(
                supervisor_type="default",
                base_instantiation_cost=0.002,
                average_runtime_seconds=60.0,
                worker_coordination_overhead=0.7
            )
        
        # Base runtime cost
        runtime_cost_per_sec = self.cost_factors.supervisor_runtime_cost_per_sec
        base_runtime_cost = runtime_cost_per_sec * estimated_runtime_seconds
        
        # Apply efficiency factor
        efficiency_multiplier = 2.0 - profile.efficiency_score  # Higher efficiency = lower cost
        
        # Apply refinement round multiplier
        refinement_multiplier = 1.0 + (refinement_rounds - 1) * 0.3
        
        total_runtime_cost = base_runtime_cost * efficiency_multiplier * refinement_multiplier
        
        return total_runtime_cost


class WorkerCoordinationCostCalculator:
    """Calculates costs for coordinating worker agents."""
    
    def __init__(self, cost_factors: HierarchicalCostFactors):
        """Initialize worker coordination cost calculator."""
        self.cost_factors = cost_factors
    
    def calculate_worker_instantiation_costs(
        self, 
        worker_count: int,
        worker_types: list[str],
        complexity_level: ComplexityLevel
    ) -> float:
        """Calculate total cost of instantiating workers."""
        
        base_cost_per_worker = self.cost_factors.worker_instantiation_cost
        
        # Complexity scaling
        complexity_multipliers = {
            ComplexityLevel.SIMPLE: 0.8,
            ComplexityLevel.MODERATE: 1.0,
            ComplexityLevel.COMPLEX: 1.2,
        }
        
        complexity_multiplier = complexity_multipliers.get(complexity_level, 1.0)
        
        # Specialized worker cost premium
        specialized_workers = len(set(worker_types))  # Unique worker types
        specialization_multiplier = 1.0 + (specialized_workers - 1) * 0.15
        
        total_cost = (
            base_cost_per_worker * 
            worker_count * 
            complexity_multiplier * 
            specialization_multiplier
        )
        
        return total_cost
    
    def calculate_coordination_overhead(
        self,
        worker_count: int,
        supervision_mode: SupervisionMode,
        estimated_runtime_seconds: float
    ) -> float:
        """Calculate coordination overhead costs."""
        
        # Base coordination events per worker per minute
        events_per_worker_per_minute = {
            SupervisionMode.SEQUENTIAL: 2,  # Less coordination needed
            SupervisionMode.PARALLEL: 4,   # More coordination needed
            SupervisionMode.HYBRID: 3,     # Balanced coordination
            SupervisionMode.ADAPTIVE: 5,   # Highest coordination
        }.get(supervision_mode, 3)
        
        # Calculate total coordination events
        runtime_minutes = estimated_runtime_seconds / 60.0
        total_events = worker_count * events_per_worker_per_minute * runtime_minutes
        
        # Coordination scales non-linearly with worker count (communication complexity)
        coordination_scaling = 1.0 + (worker_count - 1) * self.cost_factors.worker_coordination_scaling
        
        coordination_cost = (
            total_events * 
            self.cost_factors.coordination_overhead_cost * 
            coordination_scaling
        )
        
        return coordination_cost


class TalkHierCommunicationCostCalculator:
    """Calculates costs for TalkHier protocol communication."""
    
    def __init__(self, cost_factors: HierarchicalCostFactors):
        """Initialize TalkHier communication cost calculator."""
        self.cost_factors = cost_factors
    
    def calculate_communication_costs(
        self,
        estimated_tokens_per_round: int,
        refinement_rounds: int,
        worker_count: int,
        enable_consensus_building: bool = True
    ) -> dict[str, float]:
        """Calculate TalkHier communication costs."""
        
        costs = {}
        
        # Message costs scale with rounds and workers
        total_tokens = estimated_tokens_per_round * refinement_rounds * worker_count
        message_costs = total_tokens * self.cost_factors.message_cost_per_token
        costs["message_costs"] = message_costs
        
        # Refinement round overhead (each round has setup/coordination cost)
        refinement_overhead = (
            (refinement_rounds - 1) * 
            self.cost_factors.refinement_round_multiplier * 
            worker_count * 
            0.001  # Base overhead per worker per round
        )
        costs["refinement_overhead"] = refinement_overhead
        
        # Consensus building costs
        if enable_consensus_building:
            # Consensus evaluation happens after each round
            consensus_cost = refinement_rounds * self.cost_factors.consensus_building_cost
            costs["consensus_costs"] = consensus_cost
        else:
            costs["consensus_costs"] = 0.0
        
        # Cross-worker validation costs (workers review each other's outputs)
        if worker_count > 1:
            validation_interactions = worker_count * (worker_count - 1)  # All pairs
            validation_cost = validation_interactions * 0.0002  # Small cost per interaction
            costs["validation_costs"] = validation_cost
        else:
            costs["validation_costs"] = 0.0
        
        return costs
    
    def calculate_cross_supervisor_communication_cost(
        self,
        supervisor_count: int,
        estimated_tokens_per_supervisor: int
    ) -> float:
        """Calculate cost of communication between supervisors."""
        
        if supervisor_count <= 1:
            return 0.0
        
        # Cost for supervisors to share context and coordinate
        supervisor_pairs = supervisor_count * (supervisor_count - 1)
        
        communication_cost = (
            supervisor_pairs * 
            estimated_tokens_per_supervisor * 
            self.cost_factors.message_cost_per_token * 
            2.0  # Premium for cross-supervisor coordination
        )
        
        return communication_cost


class EfficiencySavingsCalculator:
    """Calculates cost savings from efficiency optimizations."""
    
    def __init__(self, cost_factors: HierarchicalCostFactors):
        """Initialize efficiency savings calculator."""
        self.cost_factors = cost_factors
    
    def calculate_efficiency_savings(
        self,
        gross_cost: float,
        enable_resource_pooling: bool = True,
        enable_caching: bool = True,
        enable_parallel_optimization: bool = True,
        worker_count: int = 1
    ) -> dict[str, float]:
        """Calculate efficiency savings from various optimizations."""
        
        savings = {}
        
        # Resource pooling savings (reuse of supervisor instances)
        if enable_resource_pooling:
            pooling_savings = gross_cost * self.cost_factors.resource_pooling_efficiency
            savings["resource_pooling"] = pooling_savings
        else:
            savings["resource_pooling"] = 0.0
        
        # Caching savings (reuse of intermediate results)
        if enable_caching:
            caching_savings = gross_cost * self.cost_factors.caching_efficiency
            savings["caching"] = caching_savings
        else:
            savings["caching"] = 0.0
        
        # Parallel execution savings (better resource utilization)
        if enable_parallel_optimization and worker_count > 1:
            parallel_savings = gross_cost * self.cost_factors.parallel_efficiency
            # Scale savings with worker count (more parallelization = more savings)
            parallel_scaling = min(worker_count / 5.0, 1.5)  # Cap at 1.5x savings
            parallel_savings *= parallel_scaling
            savings["parallel_optimization"] = parallel_savings
        else:
            savings["parallel_optimization"] = 0.0
        
        # Batch processing savings (when multiple queries processed together)
        # This would be calculated based on batch size if applicable
        savings["batch_processing"] = 0.0
        
        return savings


class HierarchicalCostOptimizer:
    """
    Main hierarchical cost optimizer that extends the base CostOptimizer.
    
    Provides comprehensive cost modeling for supervisor/worker hierarchical systems
    including all coordination, communication, and efficiency factors.
    """
    
    def __init__(
        self, 
        base_cost_optimizer: CostOptimizer,
        config: dict[str, Any] | None = None
    ):
        """Initialize hierarchical cost optimizer."""
        self.base_optimizer = base_cost_optimizer
        self.config = config or {}
        
        # Initialize hierarchical cost components
        self.cost_factors = HierarchicalCostFactors()
        self.supervisor_calculator = SupervisorCostCalculator(
            self.config.get("supervisor_calculator", {})
        )
        self.worker_calculator = WorkerCoordinationCostCalculator(self.cost_factors)
        self.communication_calculator = TalkHierCommunicationCostCalculator(self.cost_factors)
        self.efficiency_calculator = EfficiencySavingsCalculator(self.cost_factors)
        
        # Performance tracking
        self.cost_predictions: list[HierarchicalCostEstimate] = []
        self.accuracy_metrics = {
            "total_predictions": 0,
            "accurate_predictions": 0,
            "average_error": 0.0,
        }
    
    async def calculate_hierarchical_costs(
        self,
        complexity_analysis: ComplexityAnalysis,
        supervisor_type: str,
        worker_count: int,
        worker_types: list[str],
        refinement_rounds: int = 2,
        supervision_mode: SupervisionMode = SupervisionMode.PARALLEL,
        routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        enable_optimizations: bool = True
    ) -> HierarchicalCostEstimate:
        """
        Calculate comprehensive hierarchical execution costs.
        
        Args:
            complexity_analysis: Query complexity analysis
            supervisor_type: Type of supervisor to use
            worker_count: Number of workers to coordinate
            worker_types: Types of workers needed
            refinement_rounds: Number of TalkHier refinement rounds
            supervision_mode: Mode of supervision
            routing_strategy: Routing strategy affecting quality/cost tradeoffs
            enable_optimizations: Whether to apply efficiency optimizations
            
        Returns:
            Comprehensive hierarchical cost estimate
        """
        
        # Get base model cost estimate from existing optimizer
        from .cost_optimizer import OptimizationStrategy
        base_result = await self.base_optimizer.optimize(
            complexity_analysis,
            OptimizationStrategy.BALANCED
        )

        if base_result.estimated_cost:
            base_cost = base_result.estimated_cost.cost_per_request
            estimated_tokens = base_result.estimated_cost.estimated_tokens
        else:
            base_cost = 0.0
            estimated_tokens = 0
        
        # Initialize hierarchical cost estimate
        estimate = HierarchicalCostEstimate(
            base_model_cost=base_cost,
            estimated_tokens=estimated_tokens
        )
        
        # Calculate supervisor costs
        supervisor_instantiation = self.supervisor_calculator.calculate_supervisor_instantiation_cost(
            supervisor_type, worker_count, complexity_analysis.level
        )
        
        # Estimate runtime based on complexity
        estimated_runtime = self._estimate_runtime_seconds(
            complexity_analysis, worker_count, refinement_rounds
        )
        
        supervisor_runtime = self.supervisor_calculator.calculate_supervisor_runtime_cost(
            supervisor_type, estimated_runtime, refinement_rounds
        )
        
        estimate.supervisor_costs = {
            "instantiation": supervisor_instantiation,
            "runtime": supervisor_runtime,
        }
        
        # Calculate worker costs
        worker_instantiation = self.worker_calculator.calculate_worker_instantiation_costs(
            worker_count, worker_types, complexity_analysis.level
        )
        
        worker_runtime = worker_count * self.cost_factors.worker_runtime_cost_per_sec * estimated_runtime
        
        coordination_overhead = self.worker_calculator.calculate_coordination_overhead(
            worker_count, supervision_mode, estimated_runtime
        )
        
        estimate.worker_costs = {
            "instantiation": worker_instantiation,
            "runtime": worker_runtime,
        }
        estimate.coordination_costs = {"overhead": coordination_overhead}
        
        # Calculate communication costs
        tokens_per_round = max(int(estimated_tokens / 2), 500)  # Conservative estimate
        communication_costs = self.communication_calculator.calculate_communication_costs(
            tokens_per_round, refinement_rounds, worker_count, enable_consensus_building=True
        )
        estimate.communication_costs = communication_costs
        
        # Calculate efficiency savings if optimizations enabled
        if enable_optimizations:
            # Calculate gross cost first
            estimate.calculate_totals()
            efficiency_savings = self.efficiency_calculator.calculate_efficiency_savings(
                estimate.gross_cost,
                enable_resource_pooling=True,
                enable_caching=routing_strategy != RoutingStrategy.SPEED_FIRST,
                enable_parallel_optimization=supervision_mode in [SupervisionMode.PARALLEL, SupervisionMode.HYBRID],
                worker_count=worker_count
            )
            estimate.efficiency_savings = efficiency_savings
        
        # Calculate final totals
        estimate.calculate_totals()
        
        # Set performance predictions
        estimate.predicted_execution_time_seconds = estimated_runtime
        estimate.predicted_quality_score = self._predict_quality_score(
            complexity_analysis, routing_strategy, refinement_rounds
        )
        estimate.confidence = self._calculate_cost_confidence(complexity_analysis, worker_count)
        estimate.cost_variance = self._calculate_cost_variance(estimate.net_cost, complexity_analysis)
        
        # Track prediction for accuracy metrics
        self.cost_predictions.append(estimate)
        
        return estimate
    
    def _estimate_runtime_seconds(
        self,
        complexity_analysis: ComplexityAnalysis, 
        worker_count: int,
        refinement_rounds: int
    ) -> float:
        """Estimate total runtime for hierarchical execution."""
        
        # Base runtime by complexity
        base_runtime = {
            ComplexityLevel.SIMPLE: 60.0,
            ComplexityLevel.MODERATE: 180.0,
            ComplexityLevel.COMPLEX: 360.0,
        }.get(complexity_analysis.level, 180.0)
        
        # Adjust for worker count (parallel execution benefits)
        worker_efficiency = 1.0 / (1.0 + math.log(worker_count))
        worker_adjusted_runtime = base_runtime * worker_efficiency
        
        # Add refinement round time
        refinement_time = (refinement_rounds - 1) * 60.0  # 1 minute per additional round
        
        total_runtime = worker_adjusted_runtime + refinement_time
        
        return total_runtime
    
    def _predict_quality_score(
        self,
        complexity_analysis: ComplexityAnalysis,
        routing_strategy: RoutingStrategy,
        refinement_rounds: int
    ) -> float:
        """Predict quality score for hierarchical execution."""
        
        # Base quality by strategy
        base_quality = {
            RoutingStrategy.SPEED_FIRST: 0.75,
            RoutingStrategy.COST_EFFICIENT: 0.80,
            RoutingStrategy.BALANCED: 0.85,
            RoutingStrategy.QUALITY_FOCUSED: 0.93,
            RoutingStrategy.ADAPTIVE: 0.87,
        }.get(routing_strategy, 0.85)
        
        # Refinement rounds improve quality
        refinement_bonus = min((refinement_rounds - 1) * 0.03, 0.10)  # Max 10% bonus
        
        # Complexity penalty (harder queries are less accurate)
        complexity_penalty = {
            ComplexityLevel.SIMPLE: 0.0,
            ComplexityLevel.MODERATE: -0.02,
            ComplexityLevel.COMPLEX: -0.05,
        }.get(complexity_analysis.level, -0.02)
        
        predicted_quality = base_quality + refinement_bonus + complexity_penalty
        
        return max(0.6, min(0.98, predicted_quality))
    
    def _calculate_cost_confidence(
        self, complexity_analysis: ComplexityAnalysis, worker_count: int
    ) -> float:
        """Calculate confidence in cost prediction."""
        
        # Base confidence
        base_confidence = 0.85
        
        # Confidence decreases with complexity and worker count
        complexity_penalty = {
            ComplexityLevel.SIMPLE: 0.0,
            ComplexityLevel.MODERATE: -0.05,
            ComplexityLevel.COMPLEX: -0.10,
        }.get(complexity_analysis.level, -0.05)
        
        worker_penalty = -min((worker_count - 1) * 0.02, 0.15)  # Max 15% penalty
        
        confidence = base_confidence + complexity_penalty + worker_penalty
        
        return max(0.5, min(0.95, confidence))
    
    def _calculate_cost_variance(
        self, net_cost: float, complexity_analysis: ComplexityAnalysis
    ) -> float:
        """Calculate expected variance in cost prediction."""
        
        # Variance as percentage of net cost
        base_variance_pct = 0.15  # 15% base variance
        
        # Variance increases with complexity
        complexity_variance = {
            ComplexityLevel.SIMPLE: 0.05,
            ComplexityLevel.MODERATE: 0.10,
            ComplexityLevel.COMPLEX: 0.20,
        }.get(complexity_analysis.level, 0.10)
        
        total_variance_pct = base_variance_pct + complexity_variance
        variance = net_cost * total_variance_pct
        
        return variance
    
    def record_actual_cost(
        self, prediction_id: str, actual_cost: float, actual_quality: float
    ) -> None:
        """Record actual execution cost for accuracy tracking."""
        
        # Update accuracy metrics (simplified implementation)
        self.accuracy_metrics["total_predictions"] += 1
        
        # In a full implementation, this would match predictions by ID
        # and calculate detailed accuracy metrics
        logger.info(f"Recorded actual cost: {actual_cost:.6f}")
    
    async def get_cost_model_stats(self) -> dict[str, Any]:
        """Get cost model performance statistics."""
        
        return {
            "predictions": {
                "total_predictions": len(self.cost_predictions),
                "accuracy_metrics": self.accuracy_metrics,
            },
            "components": {
                "supervisor_calculator": "active",
                "worker_calculator": "active", 
                "communication_calculator": "active",
                "efficiency_calculator": "active",
            },
            "cost_factors": {
                "supervisor_instantiation": self.cost_factors.supervisor_instantiation_cost,
                "worker_instantiation": self.cost_factors.worker_instantiation_cost,
                "coordination_overhead": self.cost_factors.coordination_overhead_cost,
                "efficiency_savings": {
                    "pooling": self.cost_factors.resource_pooling_efficiency,
                    "caching": self.cost_factors.caching_efficiency,
                    "parallel": self.cost_factors.parallel_efficiency,
                }
            }
        }


__all__ = [
    "EfficiencySavingsCalculator",
    "HierarchicalCostEstimate",
    "HierarchicalCostFactors",
    "HierarchicalCostOptimizer",
    "ResourceType",
    "SupervisorCostCalculator",
    "SupervisorCostProfile",
    "TalkHierCommunicationCostCalculator",
    "WorkerCoordinationCostCalculator",
]
