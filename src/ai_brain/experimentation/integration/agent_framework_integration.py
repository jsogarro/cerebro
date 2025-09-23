"""
Agent Framework API A/B Testing Integration

This module integrates the completed Agent Framework APIs (Direct Agent, MASR,
Supervisor, TalkHier) with the A/B Testing System to enable systematic
experimentation and continuous optimization of the AI Brain platform.

This is the KEY INTEGRATION POINT that enables Cerebro to become a
self-improving AI system through experimental optimization.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import hashlib
from uuid import UUID, uuid4

# Import Agent Framework API components
from src.api.services.agent_execution_service import AgentExecutionService
from src.api.services.masr_routing_service import MASRRoutingService
from src.api.services.supervisor_coordination_service import SupervisorCoordinationService
from src.api.services.talkhier_session_service import TalkHierSessionService

# Import A/B Testing components
from ..core.unified_experiment_manager import UnifiedExperimentManager
from ..core.adaptive_allocation_engine import AdaptiveAllocationEngine
from ..core.system_experiment_registry import SystemExperimentRegistry
from ..statistical.enhanced_statistical_engine import EnhancedStatisticalEngine
from ..statistical.bayesian_experiment_design import BayesianExperimentDesign

# Import database models
from src.models.db.experiment import (
    Experiment, ExperimentVariant, ExperimentAssignment,
    ExperimentResult, ExperimentStatus, ExperimentType,
    AllocationStrategy
)

logger = logging.getLogger(__name__)


class AgentExperimentType(Enum):
    """Types of experiments for Agent Framework optimization."""
    
    # Routing experiments
    ROUTING_STRATEGY = "routing_strategy"  # MASR routing strategy optimization
    ROUTING_THRESHOLD = "routing_threshold"  # Complexity thresholds for routing
    
    # Execution pattern experiments
    API_PATTERN = "api_pattern"  # Primary vs Bypass API usage
    EXECUTION_MODE = "execution_mode"  # Chain vs Mixture vs Parallel
    
    # Supervisor experiments
    SUPERVISOR_MODE = "supervisor_mode"  # Sequential vs Parallel vs Adaptive
    WORKER_ALLOCATION = "worker_allocation"  # Worker count and allocation
    
    # TalkHier experiments
    TALKHIER_ROUNDS = "talkhier_rounds"  # Number of refinement rounds
    CONSENSUS_THRESHOLD = "consensus_threshold"  # Consensus detection threshold
    
    # Quality optimization
    QUALITY_THRESHOLD = "quality_threshold"  # Quality vs speed trade-offs
    CONFIDENCE_THRESHOLD = "confidence_threshold"  # Confidence thresholds


@dataclass
class AgentExperimentConfig:
    """Configuration for Agent Framework experiments."""
    
    experiment_id: str
    experiment_type: AgentExperimentType
    variants: Dict[str, Dict[str, Any]]  # Variant configurations
    
    # Targeting
    query_domains: List[str] = field(default_factory=lambda: ["all"])
    complexity_levels: List[str] = field(default_factory=lambda: ["all"])
    user_segments: List[str] = field(default_factory=lambda: ["all"])
    
    # Metrics to track
    primary_metric: str = "quality_score"
    secondary_metrics: List[str] = field(default_factory=lambda: [
        "latency_ms", "total_cost", "token_usage", "success_rate"
    ])
    
    # Allocation
    allocation_strategy: str = "thompson_sampling"  # Advanced multi-armed bandit
    initial_exploration_rate: float = 0.2
    
    # Statistical settings
    min_samples_per_variant: int = 100
    confidence_level: float = 0.95
    expected_effect_size: float = 0.1
    
    # Optimization goals
    optimization_goal: str = "maximize"  # maximize or minimize primary_metric
    constraints: Dict[str, float] = field(default_factory=dict)  # e.g., {"cost": 0.01}


@dataclass
class AgentExperimentResult:
    """Result from an Agent Framework experiment."""
    
    experiment_id: str
    variant_id: str
    request_id: str
    timestamp: datetime
    
    # Execution details
    api_pattern: str  # primary or bypass
    execution_mode: str  # chain, mixture, parallel
    routing_decision: Optional[Dict[str, Any]] = None
    supervisor_used: Optional[str] = None
    agents_used: List[str] = field(default_factory=list)
    
    # Metrics
    quality_score: float
    latency_ms: float
    total_cost: float
    token_usage: int
    success: bool
    
    # Additional data
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentFrameworkExperimentor:
    """
    Central integration service for A/B testing with Agent Framework APIs.
    
    This class orchestrates experiments across all agent framework components,
    enabling systematic optimization of routing, execution patterns, and
    quality metrics.
    """
    
    def __init__(self):
        """Initialize the Agent Framework Experimentor."""
        # Agent Framework services
        self.agent_service = AgentExecutionService()
        self.masr_service = MASRRoutingService()
        self.supervisor_service = SupervisorCoordinationService()
        self.talkhier_service = TalkHierSessionService()
        
        # A/B Testing components
        self.experiment_manager = UnifiedExperimentManager()
        self.allocation_engine = AdaptiveAllocationEngine()
        self.registry = SystemExperimentRegistry()
        self.statistical_engine = EnhancedStatisticalEngine()
        self.bayesian_design = BayesianExperimentDesign()
        
        # Active experiments
        self.active_experiments: Dict[str, AgentExperimentConfig] = {}
        
        # Results buffer for batch processing
        self.results_buffer: List[AgentExperimentResult] = []
        self.buffer_flush_interval = 60  # seconds
        
        # Performance tracking
        self.variant_performance: Dict[str, Dict[str, float]] = {}
        
        # Start background tasks
        self._start_background_tasks()
    
    def _start_background_tasks(self):
        """Start background tasks for result processing and optimization."""
        asyncio.create_task(self._flush_results_periodically())
        asyncio.create_task(self._update_allocations_periodically())
    
    async def _flush_results_periodically(self):
        """Periodically flush experiment results to database."""
        while True:
            await asyncio.sleep(self.buffer_flush_interval)
            await self._flush_results()
    
    async def _update_allocations_periodically(self):
        """Update variant allocations based on performance."""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            await self._update_allocations()
    
    # ==================== Experiment Setup ====================
    
    async def create_routing_experiment(
        self,
        name: str,
        strategies: List[str],
        target_domains: Optional[List[str]] = None,
        duration_days: int = 7
    ) -> str:
        """
        Create an experiment to test different MASR routing strategies.
        
        Args:
            name: Experiment name
            strategies: List of routing strategies to test
            target_domains: Specific domains to target (None = all)
            duration_days: Experiment duration
            
        Returns:
            Experiment ID
        """
        experiment_id = f"routing_{uuid4().hex[:8]}"
        
        # Create variants for each strategy
        variants = {}
        for strategy in strategies:
            variants[strategy] = {
                "routing_strategy": strategy,
                "parameters": self._get_default_strategy_params(strategy)
            }
        
        config = AgentExperimentConfig(
            experiment_id=experiment_id,
            experiment_type=AgentExperimentType.ROUTING_STRATEGY,
            variants=variants,
            query_domains=target_domains or ["all"],
            primary_metric="quality_score",
            secondary_metrics=["latency_ms", "total_cost"],
            allocation_strategy="thompson_sampling",
            optimization_goal="maximize"
        )
        
        self.active_experiments[experiment_id] = config
        
        # Register with experiment manager
        await self.experiment_manager.register_experiment(
            experiment_id=experiment_id,
            experiment_type="routing",
            variants=list(variants.keys()),
            config=config.__dict__
        )
        
        logger.info(f"Created routing experiment {experiment_id} with strategies: {strategies}")
        return experiment_id
    
    async def create_api_pattern_experiment(
        self,
        name: str,
        primary_weight: float = 0.9,
        bypass_weight: float = 0.1,
        complexity_threshold: str = "medium"
    ) -> str:
        """
        Create an experiment to optimize Primary vs Bypass API usage.
        
        Args:
            name: Experiment name
            primary_weight: Initial weight for Primary API
            bypass_weight: Initial weight for Bypass API
            complexity_threshold: Complexity level for switching
            
        Returns:
            Experiment ID
        """
        experiment_id = f"api_pattern_{uuid4().hex[:8]}"
        
        variants = {
            "primary_heavy": {
                "primary_weight": 0.95,
                "bypass_weight": 0.05,
                "switch_threshold": "high"
            },
            "balanced": {
                "primary_weight": 0.7,
                "bypass_weight": 0.3,
                "switch_threshold": "medium"
            },
            "bypass_heavy": {
                "primary_weight": 0.5,
                "bypass_weight": 0.5,
                "switch_threshold": "low"
            }
        }
        
        config = AgentExperimentConfig(
            experiment_id=experiment_id,
            experiment_type=AgentExperimentType.API_PATTERN,
            variants=variants,
            primary_metric="quality_score",
            secondary_metrics=["latency_ms", "total_cost"],
            constraints={"latency_ms": 5000, "total_cost": 0.10}
        )
        
        self.active_experiments[experiment_id] = config
        
        logger.info(f"Created API pattern experiment {experiment_id}")
        return experiment_id
    
    async def create_talkhier_optimization_experiment(
        self,
        name: str,
        min_rounds: int = 1,
        max_rounds: int = 5
    ) -> str:
        """
        Create an experiment to optimize TalkHier refinement rounds.
        
        Args:
            name: Experiment name
            min_rounds: Minimum refinement rounds
            max_rounds: Maximum refinement rounds
            
        Returns:
            Experiment ID
        """
        experiment_id = f"talkhier_{uuid4().hex[:8]}"
        
        # Create variants for different round counts
        variants = {}
        for rounds in range(min_rounds, max_rounds + 1):
            variants[f"{rounds}_rounds"] = {
                "max_rounds": rounds,
                "consensus_threshold": 0.8 - (rounds * 0.05),  # Lower threshold with more rounds
                "early_stopping": rounds > 2
            }
        
        config = AgentExperimentConfig(
            experiment_id=experiment_id,
            experiment_type=AgentExperimentType.TALKHIER_ROUNDS,
            variants=variants,
            primary_metric="consensus_quality",
            secondary_metrics=["latency_ms", "refinement_count"],
            optimization_goal="maximize"
        )
        
        self.active_experiments[experiment_id] = config
        
        logger.info(f"Created TalkHier optimization experiment {experiment_id}")
        return experiment_id
    
    # ==================== Experiment Execution ====================
    
    async def execute_with_experiment(
        self,
        query: str,
        user_id: str,
        context: Dict[str, Any],
        experiment_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Execute a query while running A/B experiments.
        
        This is the main entry point that intercepts regular API calls
        and applies experimental variations.
        
        Args:
            query: User query
            user_id: User identifier
            context: Query context
            experiment_ids: Specific experiments to run (None = all active)
            
        Returns:
            Execution result with experiment metadata
        """
        request_id = str(uuid4())
        start_time = datetime.utcnow()
        
        # Determine which experiments apply
        applicable_experiments = await self._get_applicable_experiments(
            query, context, experiment_ids
        )
        
        # Get variant assignments for each experiment
        assignments = {}
        for exp_id, config in applicable_experiments.items():
            variant = await self._assign_variant(exp_id, user_id, context)
            assignments[exp_id] = variant
        
        # Apply experimental variations
        execution_config = await self._build_execution_config(assignments)
        
        # Execute with experimental configuration
        result = await self._execute_with_config(
            query, context, execution_config
        )
        
        # Record experiment results
        end_time = datetime.utcnow()
        latency_ms = (end_time - start_time).total_seconds() * 1000
        
        for exp_id, variant_id in assignments.items():
            exp_result = AgentExperimentResult(
                experiment_id=exp_id,
                variant_id=variant_id,
                request_id=request_id,
                timestamp=start_time,
                api_pattern=execution_config.get("api_pattern", "primary"),
                execution_mode=execution_config.get("execution_mode", "chain"),
                routing_decision=result.get("routing_decision"),
                supervisor_used=result.get("supervisor"),
                agents_used=result.get("agents_used", []),
                quality_score=result.get("quality_score", 0.0),
                latency_ms=latency_ms,
                total_cost=result.get("total_cost", 0.0),
                token_usage=result.get("token_usage", 0),
                success=result.get("success", False),
                metadata={
                    "query": query,
                    "context": context,
                    "assignments": assignments
                }
            )
            self.results_buffer.append(exp_result)
        
        # Return result with experiment metadata
        result["experiments"] = {
            "request_id": request_id,
            "assignments": assignments,
            "latency_ms": latency_ms
        }
        
        return result
    
    async def _get_applicable_experiments(
        self,
        query: str,
        context: Dict[str, Any],
        experiment_ids: Optional[List[str]] = None
    ) -> Dict[str, AgentExperimentConfig]:
        """Determine which experiments apply to this query."""
        applicable = {}
        
        for exp_id, config in self.active_experiments.items():
            # Check if experiment is specified
            if experiment_ids and exp_id not in experiment_ids:
                continue
            
            # Check domain targeting
            query_domain = context.get("domain", "general")
            if "all" not in config.query_domains and query_domain not in config.query_domains:
                continue
            
            # Check complexity targeting
            complexity = context.get("complexity", "medium")
            if "all" not in config.complexity_levels and complexity not in config.complexity_levels:
                continue
            
            applicable[exp_id] = config
        
        return applicable
    
    async def _assign_variant(
        self,
        experiment_id: str,
        user_id: str,
        context: Dict[str, Any]
    ) -> str:
        """Assign user to a variant using the allocation strategy."""
        config = self.active_experiments[experiment_id]
        
        if config.allocation_strategy == "thompson_sampling":
            # Use Thompson sampling for intelligent allocation
            variant = await self.allocation_engine.allocate_thompson_sampling(
                experiment_id=experiment_id,
                variants=list(config.variants.keys()),
                performance_history=self.variant_performance.get(experiment_id, {})
            )
        elif config.allocation_strategy == "epsilon_greedy":
            # Use epsilon-greedy allocation
            variant = await self.allocation_engine.allocate_epsilon_greedy(
                experiment_id=experiment_id,
                variants=list(config.variants.keys()),
                epsilon=config.initial_exploration_rate
            )
        else:
            # Default to uniform random allocation
            import random
            variant = random.choice(list(config.variants.keys()))
        
        return variant
    
    async def _build_execution_config(
        self,
        assignments: Dict[str, str]
    ) -> Dict[str, Any]:
        """Build execution configuration from variant assignments."""
        config = {}
        
        for exp_id, variant_id in assignments.items():
            exp_config = self.active_experiments[exp_id]
            variant_config = exp_config.variants[variant_id]
            
            # Merge variant configuration
            if exp_config.experiment_type == AgentExperimentType.ROUTING_STRATEGY:
                config["routing_strategy"] = variant_config.get("routing_strategy")
                config["routing_params"] = variant_config.get("parameters", {})
            
            elif exp_config.experiment_type == AgentExperimentType.API_PATTERN:
                config["api_pattern"] = "primary" if variant_config["primary_weight"] > 0.5 else "bypass"
                config["pattern_weights"] = {
                    "primary": variant_config["primary_weight"],
                    "bypass": variant_config["bypass_weight"]
                }
            
            elif exp_config.experiment_type == AgentExperimentType.TALKHIER_ROUNDS:
                config["max_refinement_rounds"] = variant_config["max_rounds"]
                config["consensus_threshold"] = variant_config["consensus_threshold"]
                config["early_stopping"] = variant_config.get("early_stopping", False)
        
        return config
    
    async def _execute_with_config(
        self,
        query: str,
        context: Dict[str, Any],
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute query with experimental configuration."""
        # Determine API pattern
        api_pattern = config.get("api_pattern", "primary")
        
        if api_pattern == "primary":
            # Use MASR routing with experimental strategy
            routing_result = await self.masr_service.route_query(
                query=query,
                strategy=config.get("routing_strategy", "balanced"),
                parameters=config.get("routing_params", {})
            )
            
            # Execute through supervisor
            supervisor_result = await self.supervisor_service.execute_with_supervisor(
                supervisor_type=routing_result["supervisor_type"],
                task=query,
                config={
                    "max_rounds": config.get("max_refinement_rounds", 3),
                    "consensus_threshold": config.get("consensus_threshold", 0.8)
                }
            )
            
            return {
                "success": True,
                "routing_decision": routing_result,
                "supervisor": routing_result["supervisor_type"],
                "agents_used": routing_result.get("agents", []),
                "quality_score": supervisor_result.get("quality_score", 0.0),
                "total_cost": routing_result.get("estimated_cost", 0.0),
                "token_usage": supervisor_result.get("token_usage", 0),
                "result": supervisor_result.get("result")
            }
        
        else:  # bypass pattern
            # Direct agent execution
            agents = context.get("agents", ["research"])
            agent_result = await self.agent_service.execute_chain(
                agents=agents,
                query=query
            )
            
            return {
                "success": True,
                "routing_decision": None,
                "supervisor": None,
                "agents_used": agents,
                "quality_score": agent_result.get("quality_score", 0.0),
                "total_cost": agent_result.get("cost", 0.0),
                "token_usage": agent_result.get("token_usage", 0),
                "result": agent_result.get("result")
            }
    
    # ==================== Results Analysis ====================
    
    async def _flush_results(self):
        """Flush buffered experiment results to database."""
        if not self.results_buffer:
            return
        
        # Process results in batch
        results_to_save = self.results_buffer.copy()
        self.results_buffer.clear()
        
        # Update performance tracking
        for result in results_to_save:
            exp_id = result.experiment_id
            variant_id = result.variant_id
            
            if exp_id not in self.variant_performance:
                self.variant_performance[exp_id] = {}
            
            if variant_id not in self.variant_performance[exp_id]:
                self.variant_performance[exp_id][variant_id] = {
                    "successes": 0,
                    "failures": 0,
                    "total_quality": 0.0,
                    "total_cost": 0.0,
                    "total_latency": 0.0,
                    "count": 0
                }
            
            perf = self.variant_performance[exp_id][variant_id]
            if result.success:
                perf["successes"] += 1
            else:
                perf["failures"] += 1
            
            perf["total_quality"] += result.quality_score
            perf["total_cost"] += result.total_cost
            perf["total_latency"] += result.latency_ms
            perf["count"] += 1
        
        # Save to database (would implement actual DB save here)
        logger.info(f"Flushed {len(results_to_save)} experiment results")
    
    async def _update_allocations(self):
        """Update variant allocations based on performance."""
        for exp_id, config in self.active_experiments.items():
            if exp_id not in self.variant_performance:
                continue
            
            # Calculate performance metrics for each variant
            variant_scores = {}
            for variant_id, perf in self.variant_performance[exp_id].items():
                if perf["count"] > 0:
                    # Calculate composite score based on primary metric
                    if config.primary_metric == "quality_score":
                        score = perf["total_quality"] / perf["count"]
                    elif config.primary_metric == "latency_ms":
                        score = -perf["total_latency"] / perf["count"]  # Negative for minimization
                    elif config.primary_metric == "total_cost":
                        score = -perf["total_cost"] / perf["count"]  # Negative for minimization
                    else:
                        score = perf["successes"] / (perf["successes"] + perf["failures"])
                    
                    variant_scores[variant_id] = score
            
            # Update allocation engine with new scores
            if variant_scores:
                await self.allocation_engine.update_performance(
                    experiment_id=exp_id,
                    variant_scores=variant_scores
                )
    
    async def get_experiment_results(
        self,
        experiment_id: str,
        include_statistical_analysis: bool = True
    ) -> Dict[str, Any]:
        """
        Get current results and analysis for an experiment.
        
        Args:
            experiment_id: Experiment to analyze
            include_statistical_analysis: Include statistical significance testing
            
        Returns:
            Experiment results and analysis
        """
        if experiment_id not in self.active_experiments:
            raise ValueError(f"Experiment {experiment_id} not found")
        
        config = self.active_experiments[experiment_id]
        performance = self.variant_performance.get(experiment_id, {})
        
        # Calculate summary statistics
        results = {
            "experiment_id": experiment_id,
            "experiment_type": config.experiment_type.value,
            "variants": {}
        }
        
        for variant_id, perf in performance.items():
            if perf["count"] > 0:
                results["variants"][variant_id] = {
                    "sample_size": perf["count"],
                    "success_rate": perf["successes"] / (perf["successes"] + perf["failures"]),
                    "avg_quality": perf["total_quality"] / perf["count"],
                    "avg_cost": perf["total_cost"] / perf["count"],
                    "avg_latency": perf["total_latency"] / perf["count"]
                }
        
        # Statistical analysis
        if include_statistical_analysis and len(results["variants"]) > 1:
            analysis = await self.statistical_engine.analyze_experiment(
                experiment_id=experiment_id,
                variant_data=results["variants"],
                primary_metric=config.primary_metric,
                confidence_level=config.confidence_level
            )
            results["statistical_analysis"] = analysis
        
        return results
    
    async def stop_experiment(
        self,
        experiment_id: str,
        reason: str = "manual_stop"
    ) -> Dict[str, Any]:
        """
        Stop an active experiment and get final results.
        
        Args:
            experiment_id: Experiment to stop
            reason: Reason for stopping
            
        Returns:
            Final experiment results
        """
        if experiment_id not in self.active_experiments:
            raise ValueError(f"Experiment {experiment_id} not found")
        
        # Get final results
        final_results = await self.get_experiment_results(
            experiment_id, 
            include_statistical_analysis=True
        )
        
        # Remove from active experiments
        del self.active_experiments[experiment_id]
        
        # Clean up performance tracking
        if experiment_id in self.variant_performance:
            del self.variant_performance[experiment_id]
        
        # Log completion
        logger.info(f"Stopped experiment {experiment_id}: {reason}")
        
        final_results["stop_reason"] = reason
        final_results["stopped_at"] = datetime.utcnow().isoformat()
        
        return final_results
    
    # ==================== Helper Methods ====================
    
    def _get_default_strategy_params(self, strategy: str) -> Dict[str, Any]:
        """Get default parameters for a routing strategy."""
        defaults = {
            "cost_efficient": {
                "cost_weight": 0.7,
                "quality_weight": 0.3,
                "max_cost": 0.05
            },
            "quality_focused": {
                "cost_weight": 0.3,
                "quality_weight": 0.7,
                "min_quality": 0.8
            },
            "balanced": {
                "cost_weight": 0.5,
                "quality_weight": 0.5,
                "adaptive": True
            },
            "speed_optimized": {
                "max_latency": 2000,
                "parallel_execution": True,
                "cache_enabled": True
            }
        }
        return defaults.get(strategy, {})
    
    async def get_active_experiments(self) -> List[Dict[str, Any]]:
        """Get list of all active experiments."""
        experiments = []
        for exp_id, config in self.active_experiments.items():
            experiments.append({
                "id": exp_id,
                "type": config.experiment_type.value,
                "variants": list(config.variants.keys()),
                "primary_metric": config.primary_metric,
                "optimization_goal": config.optimization_goal,
                "sample_sizes": {
                    v: self.variant_performance.get(exp_id, {}).get(v, {}).get("count", 0)
                    for v in config.variants.keys()
                }
            })
        return experiments


# Singleton instance
_experimentor_instance = None


def get_experimentor() -> AgentFrameworkExperimentor:
    """Get singleton instance of the Agent Framework Experimentor."""
    global _experimentor_instance
    if _experimentor_instance is None:
        _experimentor_instance = AgentFrameworkExperimentor()
    return _experimentor_instance