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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

# Import Agent Framework API components
from src.api.services.agent_execution_service import AgentExecutionService
from src.api.services.masr_routing_service import MASRRoutingService
from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.api.services.talkhier_session_service import TalkHierSessionService

# Import database models
from ..core.adaptive_allocation_engine import AdaptiveAllocationEngine
from ..core.system_experiment_registry import SystemExperimentRegistry

# Import A/B Testing components
from ..core.unified_experiment_manager import UnifiedExperimentManager
from ..statistical.bayesian_experiment_design import BayesianExperimentDesigner
from ..statistical.enhanced_statistical_engine import EnhancedStatisticalEngine

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
    variants: dict[str, dict[str, Any]]
    query_domains: list[str] = field(default_factory=lambda: ["all"])
    complexity_levels: list[str] = field(default_factory=lambda: ["all"])
    user_segments: list[str] = field(default_factory=lambda: ["all"])
    primary_metric: str = "quality_score"
    secondary_metrics: list[str] = field(default_factory=lambda: [
        "latency_ms", "total_cost", "token_usage", "success_rate"
    ])
    allocation_strategy: str = "thompson_sampling"
    initial_exploration_rate: float = 0.2
    min_samples_per_variant: int = 100
    confidence_level: float = 0.95
    expected_effect_size: float = 0.1
    optimization_goal: str = "maximize"
    constraints: dict[str, float] = field(default_factory=dict)


@dataclass
class AgentExperimentResult:
    """Result from an Agent Framework experiment."""

    experiment_id: str
    variant_id: str
    request_id: str
    timestamp: datetime
    api_pattern: str
    execution_mode: str
    quality_score: float
    latency_ms: float
    total_cost: float
    token_usage: int
    success: bool
    routing_decision: dict[str, Any] | None = None
    supervisor_used: str | None = None
    agents_used: list[str] = field(default_factory=list)
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentFrameworkExperimentor:
    """
    Central integration service for A/B testing with Agent Framework APIs.
    
    This class orchestrates experiments across all agent framework components,
    enabling systematic optimization of routing, execution patterns, and
    quality metrics.
    """
    
    def __init__(self) -> None:
        """Initialize the Agent Framework Experimentor."""
        self.agent_service = AgentExecutionService()
        self.masr_service = MASRRoutingService()
        self.supervisor_service = SupervisorCoordinationService()
        self.talkhier_service = TalkHierSessionService()
        self.experiment_manager = UnifiedExperimentManager()
        self.allocation_engine = AdaptiveAllocationEngine()
        self.registry = SystemExperimentRegistry()
        self.statistical_engine = EnhancedStatisticalEngine()
        self.bayesian_design: BayesianExperimentDesigner | None = None
        self.active_experiments: dict[str, AgentExperimentConfig] = {}
        self.results_buffer: list[AgentExperimentResult] = []
        self.buffer_flush_interval = 60
        self.variant_performance: dict[str, dict[str, Any]] = {}
        self.variant_samples: dict[str, dict[str, dict[str, list[float]]]] = {}
        self._start_background_tasks()

    def _start_background_tasks(self) -> None:
        """Start background tasks for result processing and optimization."""
        self._flush_task = asyncio.create_task(self._flush_results_periodically())
        self._allocations_task = asyncio.create_task(self._update_allocations_periodically())
    
    async def _flush_results_periodically(self) -> None:
        """Periodically flush experiment results to database."""
        while True:
            await asyncio.sleep(self.buffer_flush_interval)
            await self._flush_results()
    
    async def _update_allocations_periodically(self) -> None:
        """Update variant allocations based on performance."""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            await self._update_allocations()
    
    # ==================== Experiment Setup ====================
    
    async def create_routing_experiment(
        self,
        name: str,
        strategies: list[str],
        target_domains: list[str] | None = None,
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

        variants: dict[str, dict[str, Any]] = {}
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

        await self.experiment_manager.create_experiment(
            experiment_config={
                "experiment_id": experiment_id,
                "experiment_type": "routing",
                "variants": list(variants.keys()),
                "config": config.__dict__
            }
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
        context: dict[str, Any],
        experiment_ids: list[str] | None = None
    ) -> dict[str, Any]:
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
        start_time = datetime.now(UTC)

        applicable_experiments = await self._get_applicable_experiments(
            query, context, experiment_ids
        )

        assignments: dict[str, str] = {}
        for exp_id in applicable_experiments:
            variant = await self._assign_variant(exp_id, user_id, context)
            assignments[exp_id] = variant

        execution_config = await self._build_execution_config(assignments)

        result = await self._execute_with_config(
            query, context, execution_config
        )

        end_time = datetime.now(UTC)
        latency_ms = (end_time - start_time).total_seconds() * 1000

        for exp_id, variant_id in assignments.items():
            exp_result = AgentExperimentResult(
                experiment_id=exp_id,
                variant_id=variant_id,
                request_id=request_id,
                timestamp=start_time,
                api_pattern=str(execution_config.get("api_pattern", "primary")),
                execution_mode=str(execution_config.get("execution_mode", "chain")),
                routing_decision=result.get("routing_decision"),
                supervisor_used=result.get("supervisor"),
                agents_used=result.get("agents_used", []),
                quality_score=float(result.get("quality_score", 0.0)),
                latency_ms=latency_ms,
                total_cost=float(result.get("total_cost", 0.0)),
                token_usage=int(result.get("token_usage", 0)),
                success=bool(result.get("success", False)),
                metadata={
                    "query": query,
                    "context": context,
                    "assignments": assignments
                }
            )
            self.results_buffer.append(exp_result)

        result["experiments"] = {
            "request_id": request_id,
            "assignments": assignments,
            "latency_ms": latency_ms
        }

        return result
    
    async def _get_applicable_experiments(
        self,
        query: str,
        context: dict[str, Any],
        experiment_ids: list[str] | None = None
    ) -> dict[str, AgentExperimentConfig]:
        """Determine which experiments apply to this query."""
        applicable: dict[str, AgentExperimentConfig] = {}

        for exp_id, config in self.active_experiments.items():
            if experiment_ids and exp_id not in experiment_ids:
                continue

            query_domain = str(context.get("domain", "general"))
            if "all" not in config.query_domains and query_domain not in config.query_domains:
                continue

            complexity = str(context.get("complexity", "medium"))
            if "all" not in config.complexity_levels and complexity not in config.complexity_levels:
                continue

            applicable[exp_id] = config

        return applicable
    
    async def _assign_variant(
        self,
        experiment_id: str,
        user_id: str,
        context: dict[str, Any]
    ) -> str:
        """Assign user to a variant using the allocation strategy."""
        import random

        config = self.active_experiments[experiment_id]

        if config.allocation_strategy == "thompson_sampling" or config.allocation_strategy == "epsilon_greedy":
            allocation_decision = await self.allocation_engine.allocate_variant(
                experiment_id=experiment_id,
                user_context=context
            )
            variant = allocation_decision.variant_id
        else:
            variant = random.choice(list(config.variants.keys()))

        return variant
    
    async def _build_execution_config(
        self,
        assignments: dict[str, str]
    ) -> dict[str, Any]:
        """Build execution configuration from variant assignments."""
        config: dict[str, Any] = {}

        for exp_id, variant_id in assignments.items():
            exp_config = self.active_experiments[exp_id]
            variant_config = exp_config.variants[variant_id]

            if exp_config.experiment_type == AgentExperimentType.ROUTING_STRATEGY:
                config["routing_strategy"] = variant_config.get("routing_strategy")
                config["routing_params"] = variant_config.get("parameters", {})

            elif exp_config.experiment_type == AgentExperimentType.API_PATTERN:
                config["api_pattern"] = "primary" if float(variant_config["primary_weight"]) > 0.5 else "bypass"
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
        context: dict[str, Any],
        config: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute query with experimental configuration."""
        api_pattern = str(config.get("api_pattern", "primary"))

        if api_pattern == "primary":
            from src.models.masr_api_models import RoutingRequest
            from src.models.supervisor_api_models import (
                CoordinationMode,
                SupervisionStrategy,
                SupervisorExecuteRequest,
            )

            routing_request = RoutingRequest(
                query=query,
                strategy=config.get("routing_strategy", "balanced"),
                context=dict(config.get("routing_params", {}))
            )
            routing_result = await self.masr_service.get_routing_decision(routing_request)

            supervisor_request = SupervisorExecuteRequest(
                query=query,
                supervision_strategy=SupervisionStrategy.COLLABORATIVE,
                coordination_mode=CoordinationMode.PARALLEL,
                max_workers=5,
                quality_threshold=float(config.get("consensus_threshold", 0.8)),
                timeout_seconds=120
            )
            # Get primary supervisor from allocations
            primary_supervisor = routing_result.supervisor_allocations[0].supervisor_type if routing_result.supervisor_allocations else "research"
            supervisor_result = await self.supervisor_service.execute_supervisor_task(
                supervisor_type=primary_supervisor,
                request=supervisor_request
            )

            return {
                "success": True,
                "routing_decision": routing_result.model_dump(),
                "supervisor": primary_supervisor,
                "agents_used": [alloc.supervisor_type for alloc in routing_result.supervisor_allocations],
                "quality_score": supervisor_result.quality_score or 0.0,
                "total_cost": routing_result.estimated_cost,
                "token_usage": 0,
                "result": supervisor_result.result
            }

        else:
            from src.models.base_models import AgentType

            from src.models.agent_api_models import AgentExecutionRequest

            agent_type = AgentType(config.get("agent_type", "RESEARCH"))
            agent_request = AgentExecutionRequest(
                query=query,
                context=context,
                parameters=dict(config.get("agent_params", {})),
                user_id=None,
                session_id=None
            )
            agent_result = await self.agent_service.execute_single_agent(
                agent_type=agent_type,
                request=agent_request
            )

            return {
                "success": True,
                "routing_decision": None,
                "supervisor": None,
                "agents_used": [agent_type.value],
                "quality_score": agent_result.quality_score,
                "total_cost": agent_result.cost_estimate or 0.0,
                "token_usage": agent_result.tokens_used or 0,
                "result": agent_result.output
            }
    
    # ==================== Results Analysis ====================
    
    async def _flush_results(self) -> None:
        """Flush buffered experiment results to database."""
        if not self.results_buffer:
            return

        results_to_save = self.results_buffer.copy()
        self.results_buffer.clear()

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
                perf["successes"] = int(perf["successes"]) + 1
            else:
                perf["failures"] = int(perf["failures"]) + 1

            perf["total_quality"] = float(perf["total_quality"]) + result.quality_score
            perf["total_cost"] = float(perf["total_cost"]) + result.total_cost
            perf["total_latency"] = float(perf["total_latency"]) + result.latency_ms
            perf["count"] = int(perf["count"]) + 1

            if exp_id not in self.variant_samples:
                self.variant_samples[exp_id] = {}
            if variant_id not in self.variant_samples[exp_id]:
                self.variant_samples[exp_id][variant_id] = {
                    "quality": [],
                    "latency": [],
                    "cost": [],
                    "success": [],
                }
            samples = self.variant_samples[exp_id][variant_id]
            samples["quality"].append(result.quality_score)
            samples["latency"].append(result.latency_ms)
            samples["cost"].append(result.total_cost)
            samples["success"].append(1.0 if result.success else 0.0)

        logger.info(f"Flushed {len(results_to_save)} experiment results")
    
    async def _update_allocations(self) -> None:
        """Update variant allocations based on performance."""
        for exp_id, config in self.active_experiments.items():
            if exp_id not in self.variant_performance:
                continue

            variant_scores: dict[str, float] = {}
            for variant_id, perf in self.variant_performance[exp_id].items():
                count = int(perf["count"])
                if count > 0:
                    if config.primary_metric == "quality_score":
                        score = float(perf["total_quality"]) / count
                    elif config.primary_metric == "latency_ms":
                        score = -float(perf["total_latency"]) / count
                    elif config.primary_metric == "total_cost":
                        score = -float(perf["total_cost"]) / count
                    else:
                        successes = int(perf["successes"])
                        failures = int(perf["failures"])
                        score = successes / (successes + failures) if (successes + failures) > 0 else 0.0

                    variant_scores[variant_id] = score

            if variant_scores:
                await self.allocation_engine.update_allocations()
    
    async def get_experiment_results(
        self,
        experiment_id: str,
        include_statistical_analysis: bool = True
    ) -> dict[str, Any]:
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

        results: dict[str, Any] = {
            "experiment_id": experiment_id,
            "experiment_type": config.experiment_type.value,
            "variants": {}
        }

        for variant_id, perf in performance.items():
            count = int(perf["count"])
            if count > 0:
                successes = int(perf["successes"])
                failures = int(perf["failures"])
                total = successes + failures
                results["variants"][variant_id] = {
                    "sample_size": count,
                    "success_rate": successes / total if total > 0 else 0.0,
                    "avg_quality": float(perf["total_quality"]) / count,
                    "avg_cost": float(perf["total_cost"]) / count,
                    "avg_latency": float(perf["total_latency"]) / count
                }

        if include_statistical_analysis and len(results["variants"]) > 1:
            experiment_data: dict[str, list[float]] = {}
            samples: dict[str, dict[str, list[float]]] = self.variant_samples.get(experiment_id, {})
            metric_key = {
                "quality_score": "quality",
                "latency_ms": "latency",
                "total_cost": "cost",
            }.get(config.primary_metric, "success")
            for variant_id in results["variants"]:
                variant_samples: dict[str, list[float]] = samples.get(variant_id, {})
                experiment_data[variant_id] = variant_samples.get(metric_key, [])

            analysis = await self.statistical_engine.comprehensive_analysis(
                experiment_data=experiment_data
            )
            results["statistical_analysis"] = analysis

        return results
    
    async def stop_experiment(
        self,
        experiment_id: str,
        reason: str = "manual_stop"
    ) -> dict[str, Any]:
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

        final_results = await self.get_experiment_results(
            experiment_id,
            include_statistical_analysis=True
        )

        del self.active_experiments[experiment_id]

        if experiment_id in self.variant_performance:
            del self.variant_performance[experiment_id]

        logger.info(f"Stopped experiment {experiment_id}: {reason}")

        final_results["stop_reason"] = reason
        final_results["stopped_at"] = datetime.now(UTC).isoformat()

        return final_results
    
    # ==================== Helper Methods ====================
    
    def _get_default_strategy_params(self, strategy: str) -> dict[str, Any]:
        """Get default parameters for a routing strategy."""
        defaults: dict[str, dict[str, Any]] = {
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
    
    async def get_active_experiments(self) -> list[dict[str, Any]]:
        """Get list of all active experiments."""
        experiments: list[dict[str, Any]] = []
        for exp_id, config in self.active_experiments.items():
            sample_sizes: dict[str, int] = {}
            for v in config.variants:
                perf = self.variant_performance.get(exp_id, {}).get(v, {})
                sample_sizes[v] = int(perf.get("count", 0))

            experiments.append({
                "id": exp_id,
                "type": config.experiment_type.value,
                "variants": list(config.variants.keys()),
                "primary_metric": config.primary_metric,
                "optimization_goal": config.optimization_goal,
                "sample_sizes": sample_sizes
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