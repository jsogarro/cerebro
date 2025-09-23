"""
Feedback Loop Optimizer for Continuous Improvement

This module implements feedback loops that automatically apply experiment
learnings back to the Agent Framework, enabling Cerebro to continuously
improve its performance through experimental optimization.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import numpy as np

# Import Agent Framework components
from src.ai_brain.router.masr import MASRouter, RoutingStrategy
from src.agents.supervisors.supervisor_factory import SupervisorFactory
from src.ai_brain.learning.supervision_feedback import SupervisionFeedbackSystem

# Import A/B Testing components
from ..integration.agent_framework_integration import (
    AgentFrameworkExperimentor,
    AgentExperimentType
)
from ..statistical.enhanced_statistical_engine import EnhancedStatisticalEngine

logger = logging.getLogger(__name__)


class OptimizationTarget(Enum):
    """Targets for optimization based on experiment results."""
    
    ROUTING_WEIGHTS = "routing_weights"  # MASR routing strategy weights
    SUPERVISOR_CONFIG = "supervisor_config"  # Supervisor execution parameters
    TALKHIER_PARAMS = "talkhier_params"  # TalkHier protocol parameters
    API_PATTERN_THRESHOLD = "api_pattern_threshold"  # Primary vs Bypass thresholds
    QUALITY_THRESHOLDS = "quality_thresholds"  # Quality control thresholds
    COST_LIMITS = "cost_limits"  # Cost optimization limits


@dataclass
class OptimizationDecision:
    """Decision made by the optimizer based on experiment results."""
    
    target: OptimizationTarget
    current_value: Any
    recommended_value: Any
    confidence: float  # 0-1 confidence in the recommendation
    expected_improvement: float  # Expected % improvement
    risk_level: str  # "low", "medium", "high"
    rationale: str
    experiment_evidence: List[str]  # Experiment IDs supporting this decision


@dataclass
class FeedbackLoopConfig:
    """Configuration for the feedback loop optimizer."""
    
    # Optimization thresholds
    min_confidence_for_auto_apply: float = 0.95  # Auto-apply if confidence >= this
    min_confidence_for_recommendation: float = 0.80  # Recommend if confidence >= this
    min_effect_size: float = 0.05  # Minimum effect size to consider
    
    # Safety settings
    max_change_per_iteration: float = 0.2  # Max 20% change per iteration
    rollback_on_degradation: bool = True
    performance_degradation_threshold: float = 0.1  # 10% degradation triggers rollback
    
    # Timing
    evaluation_interval_hours: int = 24  # Evaluate experiments every 24 hours
    min_experiment_duration_hours: int = 48  # Minimum time before concluding
    
    # Gradual rollout
    enable_gradual_rollout: bool = True
    initial_rollout_percentage: float = 10.0
    rollout_increment: float = 20.0
    
    # Learning rate
    learning_rate: float = 0.1  # How aggressively to update based on feedback


class FeedbackLoopOptimizer:
    """
    Implements continuous optimization through feedback loops.
    
    This class analyzes experiment results and automatically applies
    learnings to improve the Agent Framework's performance over time.
    """
    
    def __init__(self, config: Optional[FeedbackLoopConfig] = None):
        """Initialize the feedback loop optimizer."""
        self.config = config or FeedbackLoopConfig()
        
        # Components
        self.experimentor = AgentFrameworkExperimentor()
        self.statistical_engine = EnhancedStatisticalEngine()
        self.feedback_system = SupervisionFeedbackSystem()
        
        # State tracking
        self.active_optimizations: Dict[str, OptimizationDecision] = {}
        self.optimization_history: List[OptimizationDecision] = []
        self.rollback_states: Dict[str, Any] = {}  # For rollback capability
        
        # Performance tracking
        self.baseline_performance: Dict[str, float] = {}
        self.current_performance: Dict[str, float] = {}
        
        # Start optimization loop
        self._start_optimization_loop()
    
    def _start_optimization_loop(self):
        """Start the continuous optimization loop."""
        asyncio.create_task(self._optimization_cycle())
        asyncio.create_task(self._monitor_performance())
    
    async def _optimization_cycle(self):
        """Main optimization cycle that evaluates and applies learnings."""
        while True:
            await asyncio.sleep(self.config.evaluation_interval_hours * 3600)
            
            try:
                # Evaluate all active experiments
                await self._evaluate_experiments()
                
                # Generate optimization decisions
                decisions = await self._generate_optimization_decisions()
                
                # Apply optimizations
                for decision in decisions:
                    await self._apply_optimization(decision)
                
            except Exception as e:
                logger.error(f"Error in optimization cycle: {e}")
    
    async def _monitor_performance(self):
        """Monitor performance and trigger rollbacks if needed."""
        while True:
            await asyncio.sleep(300)  # Check every 5 minutes
            
            if self.config.rollback_on_degradation:
                await self._check_for_degradation()
    
    # ==================== Experiment Evaluation ====================
    
    async def _evaluate_experiments(self):
        """Evaluate all active experiments for optimization opportunities."""
        active_experiments = await self.experimentor.get_active_experiments()
        
        for experiment in active_experiments:
            exp_id = experiment["id"]
            exp_type = experiment["type"]
            
            # Get experiment results
            results = await self.experimentor.get_experiment_results(
                exp_id,
                include_statistical_analysis=True
            )
            
            # Check if experiment is ready for evaluation
            if await self._is_experiment_ready(results):
                await self._process_experiment_results(exp_id, exp_type, results)
    
    async def _is_experiment_ready(self, results: Dict[str, Any]) -> bool:
        """Check if experiment has enough data for reliable conclusions."""
        # Check sample sizes
        for variant_data in results.get("variants", {}).values():
            if variant_data.get("sample_size", 0) < 100:
                return False
        
        # Check statistical significance
        stats = results.get("statistical_analysis", {})
        p_value = stats.get("p_value")
        
        if p_value is None or p_value > 0.1:
            return False
        
        return True
    
    async def _process_experiment_results(
        self,
        experiment_id: str,
        experiment_type: str,
        results: Dict[str, Any]
    ):
        """Process experiment results and extract learnings."""
        stats = results.get("statistical_analysis", {})
        winning_variant = stats.get("winning_variant")
        
        if not winning_variant:
            return
        
        # Get winning configuration
        winning_config = await self._get_variant_config(experiment_id, winning_variant)
        
        # Store learnings based on experiment type
        if experiment_type == AgentExperimentType.ROUTING_STRATEGY.value:
            await self._process_routing_learnings(winning_config, stats)
        
        elif experiment_type == AgentExperimentType.API_PATTERN.value:
            await self._process_api_pattern_learnings(winning_config, stats)
        
        elif experiment_type == AgentExperimentType.TALKHIER_ROUNDS.value:
            await self._process_talkhier_learnings(winning_config, stats)
    
    # ==================== Learning Processing ====================
    
    async def _process_routing_learnings(
        self,
        winning_config: Dict[str, Any],
        stats: Dict[str, Any]
    ):
        """Process learnings from routing strategy experiments."""
        strategy = winning_config.get("routing_strategy")
        parameters = winning_config.get("parameters", {})
        
        # Update MASR router preferences
        await self.feedback_system.record_routing_performance(
            strategy=strategy,
            success_rate=stats.get("success_rate", 0),
            avg_cost=stats.get("avg_cost", 0),
            avg_quality=stats.get("avg_quality", 0)
        )
        
        logger.info(f"Learned optimal routing strategy: {strategy} with params {parameters}")
    
    async def _process_api_pattern_learnings(
        self,
        winning_config: Dict[str, Any],
        stats: Dict[str, Any]
    ):
        """Process learnings from API pattern experiments."""
        primary_weight = winning_config.get("primary_weight", 0.9)
        switch_threshold = winning_config.get("switch_threshold", "medium")
        
        # Store optimal API pattern configuration
        self.baseline_performance["api_pattern"] = {
            "primary_weight": primary_weight,
            "switch_threshold": switch_threshold,
            "performance": stats.get("avg_quality", 0)
        }
        
        logger.info(f"Learned optimal API pattern: {primary_weight:.0%} primary, switch at {switch_threshold}")
    
    async def _process_talkhier_learnings(
        self,
        winning_config: Dict[str, Any],
        stats: Dict[str, Any]
    ):
        """Process learnings from TalkHier protocol experiments."""
        max_rounds = winning_config.get("max_rounds", 3)
        consensus_threshold = winning_config.get("consensus_threshold", 0.8)
        
        # Update TalkHier parameters
        self.baseline_performance["talkhier"] = {
            "max_rounds": max_rounds,
            "consensus_threshold": consensus_threshold,
            "avg_rounds_to_consensus": stats.get("avg_rounds", 0)
        }
        
        logger.info(f"Learned optimal TalkHier: {max_rounds} rounds, {consensus_threshold} consensus")
    
    # ==================== Optimization Decision Generation ====================
    
    async def _generate_optimization_decisions(self) -> List[OptimizationDecision]:
        """Generate optimization decisions based on accumulated learnings."""
        decisions = []
        
        # Check routing optimizations
        routing_decision = await self._generate_routing_optimization()
        if routing_decision:
            decisions.append(routing_decision)
        
        # Check supervisor optimizations
        supervisor_decision = await self._generate_supervisor_optimization()
        if supervisor_decision:
            decisions.append(supervisor_decision)
        
        # Check quality threshold optimizations
        quality_decision = await self._generate_quality_optimization()
        if quality_decision:
            decisions.append(quality_decision)
        
        return decisions
    
    async def _generate_routing_optimization(self) -> Optional[OptimizationDecision]:
        """Generate routing strategy optimization decision."""
        # Get current and recommended strategies from feedback system
        current_weights = await self._get_current_routing_weights()
        recommended_weights = await self.feedback_system.get_optimal_routing_weights()
        
        if not recommended_weights:
            return None
        
        # Calculate expected improvement
        current_performance = await self._estimate_performance(current_weights)
        expected_performance = await self._estimate_performance(recommended_weights)
        improvement = ((expected_performance - current_performance) / current_performance) * 100
        
        # Check if improvement is significant
        if abs(improvement) < self.config.min_effect_size * 100:
            return None
        
        return OptimizationDecision(
            target=OptimizationTarget.ROUTING_WEIGHTS,
            current_value=current_weights,
            recommended_value=recommended_weights,
            confidence=0.85,  # Would calculate based on experiment data
            expected_improvement=improvement,
            risk_level="low" if improvement > 0 else "medium",
            rationale=f"Experiment data shows {improvement:.1f}% improvement with new weights",
            experiment_evidence=["routing_exp_001", "routing_exp_002"]  # Would get actual IDs
        )
    
    async def _generate_supervisor_optimization(self) -> Optional[OptimizationDecision]:
        """Generate supervisor configuration optimization decision."""
        # Analyze supervisor performance across experiments
        supervisor_data = self.baseline_performance.get("supervisor", {})
        
        if not supervisor_data:
            return None
        
        current_config = await self._get_current_supervisor_config()
        
        # Generate optimized configuration
        optimized_config = {
            "execution_mode": supervisor_data.get("best_mode", "adaptive"),
            "max_workers": supervisor_data.get("optimal_workers", 3),
            "quality_threshold": supervisor_data.get("quality_threshold", 0.8)
        }
        
        # Check if changes are significant
        if current_config == optimized_config:
            return None
        
        return OptimizationDecision(
            target=OptimizationTarget.SUPERVISOR_CONFIG,
            current_value=current_config,
            recommended_value=optimized_config,
            confidence=0.78,
            expected_improvement=5.0,  # Would calculate properly
            risk_level="low",
            rationale="Supervisor optimization based on coordination experiments",
            experiment_evidence=["supervisor_exp_001"]
        )
    
    async def _generate_quality_optimization(self) -> Optional[OptimizationDecision]:
        """Generate quality threshold optimization decision."""
        # Analyze quality vs speed trade-offs
        quality_data = await self._analyze_quality_tradeoffs()
        
        if not quality_data:
            return None
        
        current_threshold = await self._get_current_quality_threshold()
        optimal_threshold = quality_data.get("optimal_threshold", 0.85)
        
        if abs(current_threshold - optimal_threshold) < 0.05:
            return None
        
        return OptimizationDecision(
            target=OptimizationTarget.QUALITY_THRESHOLDS,
            current_value=current_threshold,
            recommended_value=optimal_threshold,
            confidence=0.82,
            expected_improvement=3.0,
            risk_level="medium",
            rationale=f"Optimal quality threshold balances speed and accuracy",
            experiment_evidence=["quality_exp_001"]
        )
    
    # ==================== Optimization Application ====================
    
    async def _apply_optimization(self, decision: OptimizationDecision):
        """Apply an optimization decision to the system."""
        # Check confidence threshold
        if decision.confidence < self.config.min_confidence_for_recommendation:
            logger.info(f"Skipping optimization {decision.target} - confidence too low")
            return
        
        # Store rollback state
        self.rollback_states[decision.target.value] = decision.current_value
        
        # Apply based on confidence level
        if decision.confidence >= self.config.min_confidence_for_auto_apply:
            await self._auto_apply_optimization(decision)
        else:
            await self._recommend_optimization(decision)
    
    async def _auto_apply_optimization(self, decision: OptimizationDecision):
        """Automatically apply high-confidence optimizations."""
        logger.info(f"Auto-applying optimization: {decision.target.value}")
        
        if self.config.enable_gradual_rollout:
            # Gradual rollout
            await self._gradual_rollout(decision)
        else:
            # Direct application
            await self._direct_apply(decision)
        
        # Track optimization
        self.active_optimizations[decision.target.value] = decision
        self.optimization_history.append(decision)
    
    async def _recommend_optimization(self, decision: OptimizationDecision):
        """Recommend optimization for manual review."""
        logger.info(f"Recommending optimization: {decision.target.value}")
        
        # Store recommendation for review
        recommendation = {
            "timestamp": datetime.utcnow().isoformat(),
            "decision": decision,
            "requires_approval": True
        }
        
        # Would send to dashboard or notification system
        logger.info(f"Optimization recommendation: {recommendation}")
    
    async def _gradual_rollout(self, decision: OptimizationDecision):
        """Gradually roll out optimization with increasing traffic."""
        rollout_percentage = self.config.initial_rollout_percentage
        
        while rollout_percentage <= 100:
            # Apply to percentage of traffic
            await self._apply_partial_optimization(decision, rollout_percentage)
            
            # Monitor for issues
            await asyncio.sleep(3600)  # Wait 1 hour
            
            # Check performance
            degradation = await self._check_rollout_performance(decision)
            if degradation:
                logger.warning(f"Degradation detected during rollout, rolling back")
                await self._rollback_optimization(decision)
                return
            
            # Increase rollout
            rollout_percentage += self.config.rollout_increment
        
        logger.info(f"Successful gradual rollout of {decision.target.value}")
    
    async def _direct_apply(self, decision: OptimizationDecision):
        """Directly apply optimization to all traffic."""
        if decision.target == OptimizationTarget.ROUTING_WEIGHTS:
            await self._apply_routing_weights(decision.recommended_value)
        
        elif decision.target == OptimizationTarget.SUPERVISOR_CONFIG:
            await self._apply_supervisor_config(decision.recommended_value)
        
        elif decision.target == OptimizationTarget.TALKHIER_PARAMS:
            await self._apply_talkhier_params(decision.recommended_value)
        
        elif decision.target == OptimizationTarget.QUALITY_THRESHOLDS:
            await self._apply_quality_threshold(decision.recommended_value)
    
    # ==================== Rollback and Safety ====================
    
    async def _check_for_degradation(self):
        """Check if any optimizations are causing performance degradation."""
        for target, decision in self.active_optimizations.items():
            current_perf = await self._get_current_performance(target)
            baseline_perf = self.baseline_performance.get(target, {}).get("performance", 0)
            
            if baseline_perf > 0:
                degradation = (baseline_perf - current_perf) / baseline_perf
                
                if degradation > self.config.performance_degradation_threshold:
                    logger.warning(f"Performance degradation detected for {target}: {degradation:.1%}")
                    await self._rollback_optimization(decision)
    
    async def _rollback_optimization(self, decision: OptimizationDecision):
        """Rollback an optimization to previous state."""
        logger.info(f"Rolling back optimization: {decision.target.value}")
        
        previous_value = self.rollback_states.get(decision.target.value)
        if previous_value:
            await self._direct_apply(
                OptimizationDecision(
                    target=decision.target,
                    current_value=decision.recommended_value,
                    recommended_value=previous_value,
                    confidence=1.0,
                    expected_improvement=0,
                    risk_level="low",
                    rationale="Rollback due to performance degradation",
                    experiment_evidence=[]
                )
            )
        
        # Remove from active optimizations
        self.active_optimizations.pop(decision.target.value, None)
    
    # ==================== Helper Methods ====================
    
    async def _get_current_routing_weights(self) -> Dict[str, float]:
        """Get current MASR routing weights."""
        # Would integrate with actual MASR router
        return {
            "cost_weight": 0.5,
            "quality_weight": 0.5,
            "speed_weight": 0.0
        }
    
    async def _get_current_supervisor_config(self) -> Dict[str, Any]:
        """Get current supervisor configuration."""
        # Would integrate with supervisor factory
        return {
            "execution_mode": "sequential",
            "max_workers": 3,
            "quality_threshold": 0.8
        }
    
    async def _get_current_quality_threshold(self) -> float:
        """Get current quality threshold."""
        return 0.85
    
    async def _estimate_performance(self, config: Dict[str, Any]) -> float:
        """Estimate performance for a given configuration."""
        # Would use historical data and ML models
        return np.random.uniform(0.7, 0.95)
    
    async def _analyze_quality_tradeoffs(self) -> Dict[str, Any]:
        """Analyze quality vs speed trade-offs from experiments."""
        # Would analyze experiment data
        return {
            "optimal_threshold": 0.87,
            "speed_impact": -15,  # % slower
            "quality_impact": 10   # % better
        }
    
    async def _get_variant_config(self, experiment_id: str, variant_id: str) -> Dict[str, Any]:
        """Get configuration for a specific variant."""
        # Would retrieve from experiment manager
        return {}
    
    async def _apply_partial_optimization(
        self,
        decision: OptimizationDecision,
        percentage: float
    ):
        """Apply optimization to a percentage of traffic."""
        logger.info(f"Applying {decision.target.value} to {percentage}% of traffic")
        # Would implement traffic splitting logic
    
    async def _check_rollout_performance(self, decision: OptimizationDecision) -> bool:
        """Check if rollout is causing degradation."""
        # Would check metrics for rolled out traffic
        return False
    
    async def _get_current_performance(self, target: str) -> float:
        """Get current performance metric for a target."""
        # Would get from monitoring system
        return np.random.uniform(0.7, 0.95)
    
    async def _apply_routing_weights(self, weights: Dict[str, float]):
        """Apply new routing weights to MASR."""
        logger.info(f"Applying routing weights: {weights}")
        # Would update MASR configuration
    
    async def _apply_supervisor_config(self, config: Dict[str, Any]):
        """Apply new supervisor configuration."""
        logger.info(f"Applying supervisor config: {config}")
        # Would update supervisor factory
    
    async def _apply_talkhier_params(self, params: Dict[str, Any]):
        """Apply new TalkHier parameters."""
        logger.info(f"Applying TalkHier params: {params}")
        # Would update TalkHier service
    
    async def _apply_quality_threshold(self, threshold: float):
        """Apply new quality threshold."""
        logger.info(f"Applying quality threshold: {threshold}")
        # Would update quality control system


# Singleton instance
_optimizer_instance = None


def get_feedback_optimizer() -> FeedbackLoopOptimizer:
    """Get singleton instance of the feedback loop optimizer."""
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = FeedbackLoopOptimizer()
    return _optimizer_instance