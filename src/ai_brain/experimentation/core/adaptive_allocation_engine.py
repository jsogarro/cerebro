"""
Adaptive Allocation Engine for Multi-Armed Bandit Integration

This engine provides sophisticated allocation strategies for experiments using
multi-armed bandit algorithms. It integrates with the existing statistical
framework to provide real-time adaptive allocation based on performance.

Features:
- Thompson Sampling for Bayesian optimization
- Upper Confidence Bound (UCB) for balanced exploration/exploitation
- Contextual bandits using query complexity and domain features
- Real-time traffic allocation with safety constraints
- Integration with existing MASR routing and Agent Framework APIs
"""

import asyncio
import logging
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
from enum import Enum
import random

from .unified_experiment_manager import ExperimentType, ExperimentStatus, SystemComponent
from ..statistical.enhanced_statistical_engine import (
    BanditAlgorithm, BanditResult, MultiBanditOptimizer
)

logger = logging.getLogger(__name__)


class AllocationStrategy(Enum):
    """Allocation strategies for experiments."""
    
    FIXED_RANDOM = "fixed_random"           # Fixed percentage allocation
    ADAPTIVE_BANDIT = "adaptive_bandit"     # Multi-armed bandit adaptation
    CONTEXTUAL_BANDIT = "contextual_bandit" # Context-aware allocation
    SAFETY_CONSTRAINED = "safety_constrained" # Gradual rollout with safety checks
    BAYESIAN_OPTIMAL = "bayesian_optimal"   # Bayesian optimization


@dataclass
class AllocationConfig:
    """Configuration for allocation strategies."""
    
    strategy: AllocationStrategy
    initial_allocation: Dict[str, float]  # Initial traffic percentages
    min_allocation: float = 0.05          # Minimum allocation per variant (safety)
    max_allocation: float = 0.70          # Maximum allocation per variant (safety)
    
    # Bandit-specific parameters
    exploration_rate: float = 0.1
    confidence_threshold: float = 0.95
    update_frequency_seconds: int = 300   # 5 minutes
    
    # Safety parameters
    enable_guardrails: bool = True
    performance_threshold: float = 0.95   # Minimum performance vs control
    safety_sample_size: int = 100         # Minimum samples before adaptation
    
    # Context features for contextual bandits
    context_features: List[str] = field(default_factory=list)


@dataclass
class AllocationDecision:
    """Result of allocation decision."""
    
    experiment_id: str
    variant_id: str
    allocation_probability: float
    allocation_strategy: AllocationStrategy
    
    # Decision context
    context: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    
    # Performance tracking
    expected_reward: float = 0.0
    exploration_bonus: float = 0.0
    
    # Safety information
    safety_check_passed: bool = True
    safety_warnings: List[str] = field(default_factory=list)
    
    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    allocation_reason: str = ""


class AdaptiveAllocationEngine:
    """
    Engine for adaptive experiment allocation using multi-armed bandits.
    
    Provides intelligent allocation strategies that adapt based on performance
    while maintaining safety constraints and statistical rigor.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize adaptive allocation engine."""
        self.config = config or {}
        
        # Allocation state
        self.active_experiments: Dict[str, AllocationConfig] = {}
        self.bandit_optimizers: Dict[str, MultiBanditOptimizer] = {}
        self.allocation_history: List[AllocationDecision] = []
        
        # Performance tracking
        self.allocation_stats = {
            "total_allocations": 0,
            "successful_adaptations": 0,
            "safety_interventions": 0,
            "average_regret": 0.0,
        }
        
        # Safety configuration
        self.enable_safety = self.config.get("enable_safety", True)
        self.global_min_allocation = self.config.get("global_min_allocation", 0.05)
        self.global_max_allocation = self.config.get("global_max_allocation", 0.70)
        
        # Update scheduling
        self.update_task: Optional[asyncio.Task] = None
        self.update_interval = self.config.get("update_interval_seconds", 300)
    
    async def register_experiment(
        self, 
        experiment_id: str,
        variants: List[str],
        allocation_config: AllocationConfig
    ):
        """
        Register experiment for adaptive allocation.
        
        Args:
            experiment_id: Unique experiment identifier
            variants: List of variant names
            allocation_config: Configuration for allocation strategy
        """
        
        # Validate configuration
        if sum(allocation_config.initial_allocation.values()) != 1.0:
            raise ValueError("Initial allocation must sum to 1.0")
        
        if not all(variant in allocation_config.initial_allocation for variant in variants):
            raise ValueError("All variants must have initial allocation")
        
        # Store configuration
        self.active_experiments[experiment_id] = allocation_config
        
        # Initialize bandit optimizer if using bandit strategies
        if allocation_config.strategy in [
            AllocationStrategy.ADAPTIVE_BANDIT,
            AllocationStrategy.CONTEXTUAL_BANDIT,
            AllocationStrategy.BAYESIAN_OPTIMAL,
        ]:
            bandit = MultiBanditOptimizer({"exploration_rate": allocation_config.exploration_rate})
            await bandit.initialize_bandit(
                num_arms=len(variants),
                algorithm=self._get_bandit_algorithm(allocation_config.strategy),
                context_features=allocation_config.context_features
            )
            self.bandit_optimizers[experiment_id] = bandit
        
        logger.info(f"Registered experiment {experiment_id} with {len(variants)} variants")
    
    async def allocate_variant(
        self,
        experiment_id: str,
        user_context: Optional[Dict[str, Any]] = None
    ) -> AllocationDecision:
        """
        Allocate user to experiment variant.
        
        Args:
            experiment_id: Experiment to allocate for
            user_context: User context for contextual allocation
            
        Returns:
            Allocation decision with variant and reasoning
        """
        
        if experiment_id not in self.active_experiments:
            raise ValueError(f"Experiment {experiment_id} not registered")
        
        config = self.active_experiments[experiment_id]
        context = user_context or {}
        
        # Choose allocation method based on strategy
        if config.strategy == AllocationStrategy.FIXED_RANDOM:
            decision = await self._fixed_random_allocation(experiment_id, config, context)
        elif config.strategy == AllocationStrategy.ADAPTIVE_BANDIT:
            decision = await self._adaptive_bandit_allocation(experiment_id, config, context)
        elif config.strategy == AllocationStrategy.CONTEXTUAL_BANDIT:
            decision = await self._contextual_bandit_allocation(experiment_id, config, context)
        elif config.strategy == AllocationStrategy.SAFETY_CONSTRAINED:
            decision = await self._safety_constrained_allocation(experiment_id, config, context)
        else:
            # Default to fixed random
            decision = await self._fixed_random_allocation(experiment_id, config, context)
        
        # Apply safety checks
        if self.enable_safety:
            decision = await self._apply_safety_checks(decision, config)
        
        # Record allocation
        self.allocation_history.append(decision)
        self.allocation_stats["total_allocations"] += 1
        
        # Trim history to prevent memory growth
        if len(self.allocation_history) > 10000:
            self.allocation_history = self.allocation_history[-5000:]
        
        logger.debug(f"Allocated {decision.variant_id} for experiment {experiment_id}")
        
        return decision
    
    async def _fixed_random_allocation(
        self,
        experiment_id: str,
        config: AllocationConfig,
        context: Dict[str, Any]
    ) -> AllocationDecision:
        """Fixed random allocation based on initial configuration."""
        
        # Select variant based on fixed probabilities
        variants = list(config.initial_allocation.keys())
        probabilities = list(config.initial_allocation.values())
        
        selected_variant = np.random.choice(variants, p=probabilities)
        allocation_prob = config.initial_allocation[selected_variant]
        
        return AllocationDecision(
            experiment_id=experiment_id,
            variant_id=selected_variant,
            allocation_probability=allocation_prob,
            allocation_strategy=config.strategy,
            context=context,
            confidence=0.8,  # High confidence for fixed allocation
            allocation_reason="Fixed random allocation based on initial configuration"
        )
    
    async def _adaptive_bandit_allocation(
        self,
        experiment_id: str,
        config: AllocationConfig,
        context: Dict[str, Any]
    ) -> AllocationDecision:
        """Adaptive allocation using multi-armed bandit."""
        
        bandit = self.bandit_optimizers.get(experiment_id)
        if not bandit:
            # Fallback to fixed allocation
            return await self._fixed_random_allocation(experiment_id, config, context)
        
        # Get bandit recommendation
        bandit_result = await bandit.select_arm(context)
        
        # Map arm index to variant
        variants = list(config.initial_allocation.keys())
        selected_variant = variants[bandit_result.selected_arm]
        
        return AllocationDecision(
            experiment_id=experiment_id,
            variant_id=selected_variant,
            allocation_probability=bandit_result.arm_probabilities[bandit_result.selected_arm],
            allocation_strategy=config.strategy,
            context=context,
            confidence=bandit_result.confidence,
            expected_reward=bandit_result.expected_reward,
            exploration_bonus=bandit_result.exploration_rate,
            allocation_reason=f"Multi-armed bandit selection (algorithm: {bandit_result.algorithm.value})"
        )
    
    async def _contextual_bandit_allocation(
        self,
        experiment_id: str,
        config: AllocationConfig,
        context: Dict[str, Any]
    ) -> AllocationDecision:
        """Contextual bandit allocation using user/query context."""
        
        # Extract context features
        context_vector = []
        for feature in config.context_features:
            value = context.get(feature, 0.0)
            if isinstance(value, str):
                # Convert string features to numeric (simplified)
                value = hash(value) % 100 / 100.0
            context_vector.append(float(value))
        
        # Use bandit with context
        bandit = self.bandit_optimizers.get(experiment_id)
        if bandit:
            bandit_result = await bandit.select_arm({"features": context_vector})
            
            variants = list(config.initial_allocation.keys())
            selected_variant = variants[bandit_result.selected_arm]
            
            return AllocationDecision(
                experiment_id=experiment_id,
                variant_id=selected_variant,
                allocation_probability=bandit_result.arm_probabilities[bandit_result.selected_arm],
                allocation_strategy=config.strategy,
                context=context,
                confidence=bandit_result.confidence,
                expected_reward=bandit_result.expected_reward,
                allocation_reason=f"Contextual bandit selection using features: {config.context_features}"
            )
        
        # Fallback to adaptive bandit
        return await self._adaptive_bandit_allocation(experiment_id, config, context)
    
    async def _safety_constrained_allocation(
        self,
        experiment_id: str,
        config: AllocationConfig,
        context: Dict[str, Any]
    ) -> AllocationDecision:
        """Safety-constrained allocation with gradual rollout."""
        
        # Check if we have enough safety samples
        total_samples = sum(len(rewards) for rewards in getattr(
            self.bandit_optimizers.get(experiment_id, MultiBanditOptimizer()), 'arm_rewards', []
        ))
        
        if total_samples < config.safety_sample_size:
            # Use conservative fixed allocation during safety period
            return await self._fixed_random_allocation(experiment_id, config, context)
        
        # Use bandit allocation with safety constraints
        decision = await self._adaptive_bandit_allocation(experiment_id, config, context)
        
        # Apply safety constraints
        if decision.allocation_probability < config.min_allocation:
            decision.allocation_probability = config.min_allocation
            decision.safety_warnings.append("Increased allocation to meet minimum safety threshold")
        
        if decision.allocation_probability > config.max_allocation:
            decision.allocation_probability = config.max_allocation
            decision.safety_warnings.append("Reduced allocation to meet maximum safety threshold")
        
        decision.allocation_reason += " (with safety constraints)"
        
        return decision
    
    async def _apply_safety_checks(
        self,
        decision: AllocationDecision,
        config: AllocationConfig
    ) -> AllocationDecision:
        """Apply safety checks to allocation decision."""
        
        if not config.enable_guardrails:
            return decision
        
        # Check global allocation bounds
        if decision.allocation_probability < self.global_min_allocation:
            decision.allocation_probability = self.global_min_allocation
            decision.safety_warnings.append("Applied global minimum allocation limit")
            decision.safety_check_passed = False
        
        if decision.allocation_probability > self.global_max_allocation:
            decision.allocation_probability = self.global_max_allocation
            decision.safety_warnings.append("Applied global maximum allocation limit")
            decision.safety_check_passed = False
        
        # Check performance threshold (if we have historical data)
        bandit = self.bandit_optimizers.get(decision.experiment_id)
        if bandit and hasattr(bandit, 'arm_values'):
            # Find control variant (usually first)
            control_performance = bandit.arm_values[0] if bandit.arm_values else 0.5
            
            # Get selected variant performance
            variants = list(config.initial_allocation.keys())
            try:
                selected_arm_index = variants.index(decision.variant_id)
                variant_performance = bandit.arm_values[selected_arm_index] if bandit.arm_values else 0.5
                
                # Check performance threshold
                relative_performance = variant_performance / max(control_performance, 0.01)
                
                if relative_performance < config.performance_threshold:
                    decision.safety_warnings.append(
                        f"Variant performance ({relative_performance:.3f}) below threshold ({config.performance_threshold})"
                    )
                    decision.safety_check_passed = False
            except (ValueError, IndexError):
                # Variant not found in list
                decision.safety_warnings.append("Variant not found in bandit arms")
        
        return decision
    
    async def record_outcome(
        self,
        experiment_id: str,
        variant_id: str,
        reward: float,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        Record experiment outcome for bandit learning.
        
        Args:
            experiment_id: Experiment identifier
            variant_id: Variant that was selected
            reward: Observed reward (0.0 - 1.0)
            context: Context features for contextual bandits
        """
        
        config = self.active_experiments.get(experiment_id)
        bandit = self.bandit_optimizers.get(experiment_id)
        
        if not config or not bandit:
            logger.warning(f"Cannot record outcome for unknown experiment {experiment_id}")
            return
        
        # Map variant to arm index
        variants = list(config.initial_allocation.keys())
        try:
            arm_index = variants.index(variant_id)
        except ValueError:
            logger.error(f"Variant {variant_id} not found for experiment {experiment_id}")
            return
        
        # Update bandit with outcome
        await bandit.update_bandit(arm_index, reward)
        
        # Update statistics
        if reward > 0.5:  # Consider above 0.5 as success
            self.allocation_stats["successful_adaptations"] += 1
        
        logger.debug(f"Recorded outcome for {experiment_id}: {variant_id} = {reward}")
    
    async def update_allocations(self):
        """Update all active experiment allocations based on performance."""
        
        updated_experiments = []
        
        for experiment_id, config in self.active_experiments.items():
            if config.strategy not in [
                AllocationStrategy.ADAPTIVE_BANDIT,
                AllocationStrategy.CONTEXTUAL_BANDIT,
                AllocationStrategy.BAYESIAN_OPTIMAL,
            ]:
                continue  # Skip non-adaptive strategies
            
            bandit = self.bandit_optimizers.get(experiment_id)
            if not bandit:
                continue
            
            try:
                # Calculate new optimal allocation
                new_allocation = await self._calculate_optimal_allocation(experiment_id, config, bandit)
                
                # Apply safety constraints
                safe_allocation = self._apply_allocation_safety(new_allocation, config)
                
                # Update configuration
                old_allocation = config.initial_allocation.copy()
                config.initial_allocation = safe_allocation
                
                # Log significant changes
                max_change = max(
                    abs(safe_allocation[variant] - old_allocation.get(variant, 0))
                    for variant in safe_allocation.keys()
                )
                
                if max_change > 0.1:  # 10% change threshold
                    logger.info(f"Updated allocations for {experiment_id}: {safe_allocation}")
                    updated_experiments.append(experiment_id)
                
            except Exception as e:
                logger.error(f"Failed to update allocation for {experiment_id}: {e}")
        
        return updated_experiments
    
    async def _calculate_optimal_allocation(
        self,
        experiment_id: str,
        config: AllocationConfig,
        bandit: MultiBanditOptimizer
    ) -> Dict[str, float]:
        """Calculate optimal allocation based on bandit performance."""
        
        if not hasattr(bandit, 'arm_values') or not bandit.arm_values:
            return config.initial_allocation.copy()
        
        variants = list(config.initial_allocation.keys())
        arm_values = bandit.arm_values
        arm_counts = bandit.arm_counts
        
        # Calculate allocation based on Thompson sampling probabilities
        if config.strategy == AllocationStrategy.ADAPTIVE_BANDIT:
            # Use softmax of arm values for allocation
            exp_values = np.exp(np.array(arm_values) * 2)  # Temperature = 0.5
            probabilities = exp_values / np.sum(exp_values)
            
        elif config.strategy == AllocationStrategy.CONTEXTUAL_BANDIT:
            # Use confidence-weighted allocation
            confidences = [count / max(sum(arm_counts), 1) for count in arm_counts]
            weighted_values = [val * conf for val, conf in zip(arm_values, confidences)]
            
            exp_values = np.exp(np.array(weighted_values) * 2)
            probabilities = exp_values / np.sum(exp_values)
            
        else:  # BAYESIAN_OPTIMAL
            # Bayesian optimal allocation (Thompson sampling)
            probabilities = bandit.arm_probabilities
        
        # Convert to allocation dictionary
        allocation = {}
        for i, variant in enumerate(variants):
            allocation[variant] = probabilities[i] if i < len(probabilities) else 0.0
        
        # Normalize to ensure sum = 1.0
        total_prob = sum(allocation.values())
        if total_prob > 0:
            allocation = {k: v / total_prob for k, v in allocation.items()}
        
        return allocation
    
    def _apply_allocation_safety(
        self,
        allocation: Dict[str, float],
        config: AllocationConfig
    ) -> Dict[str, float]:
        """Apply safety constraints to allocation."""
        
        safe_allocation = allocation.copy()
        
        # Apply minimum allocation constraints
        for variant in safe_allocation:
            if safe_allocation[variant] < config.min_allocation:
                safe_allocation[variant] = config.min_allocation
        
        # Apply maximum allocation constraints
        for variant in safe_allocation:
            if safe_allocation[variant] > config.max_allocation:
                safe_allocation[variant] = config.max_allocation
        
        # Renormalize
        total = sum(safe_allocation.values())
        if total != 1.0:
            safe_allocation = {k: v / total for k, v in safe_allocation.items()}
        
        return safe_allocation
    
    def _get_bandit_algorithm(self, strategy: AllocationStrategy) -> BanditAlgorithm:
        """Map allocation strategy to bandit algorithm."""
        
        mapping = {
            AllocationStrategy.ADAPTIVE_BANDIT: BanditAlgorithm.THOMPSON_SAMPLING,
            AllocationStrategy.CONTEXTUAL_BANDIT: BanditAlgorithm.CONTEXTUAL_BANDIT,
            AllocationStrategy.BAYESIAN_OPTIMAL: BanditAlgorithm.THOMPSON_SAMPLING,
            AllocationStrategy.SAFETY_CONSTRAINED: BanditAlgorithm.UPPER_CONFIDENCE_BOUND,
        }
        
        return mapping.get(strategy, BanditAlgorithm.EPSILON_GREEDY)
    
    async def start_adaptive_updates(self):
        """Start background task for adaptive allocation updates."""
        
        if self.update_task and not self.update_task.done():
            logger.warning("Adaptive updates already running")
            return
        
        self.update_task = asyncio.create_task(self._adaptive_update_loop())
        logger.info("Started adaptive allocation updates")
    
    async def stop_adaptive_updates(self):
        """Stop background adaptive updates."""
        
        if self.update_task and not self.update_task.done():
            self.update_task.cancel()
            try:
                await self.update_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Stopped adaptive allocation updates")
    
    async def _adaptive_update_loop(self):
        """Background loop for updating allocations."""
        
        while True:
            try:
                await asyncio.sleep(self.update_interval)
                
                # Update allocations for all adaptive experiments
                updated = await self.update_allocations()
                
                if updated:
                    logger.info(f"Updated allocations for {len(updated)} experiments")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in adaptive update loop: {e}")
                await asyncio.sleep(60)  # Wait before retrying
    
    async def get_experiment_performance(self, experiment_id: str) -> Dict[str, Any]:
        """Get performance metrics for experiment."""
        
        config = self.active_experiments.get(experiment_id)
        bandit = self.bandit_optimizers.get(experiment_id)
        
        if not config or not bandit:
            return {"error": f"Experiment {experiment_id} not found"}
        
        variants = list(config.initial_allocation.keys())
        
        performance = {
            "experiment_id": experiment_id,
            "strategy": config.strategy.value,
            "variants": {},
            "total_samples": sum(getattr(bandit, 'arm_counts', [])),
            "regret": getattr(bandit, 'regret', 0.0),
            "current_allocation": config.initial_allocation.copy(),
        }
        
        # Add per-variant statistics
        if hasattr(bandit, 'arm_values') and hasattr(bandit, 'arm_counts'):
            for i, variant in enumerate(variants):
                if i < len(bandit.arm_values):
                    performance["variants"][variant] = {
                        "average_reward": bandit.arm_values[i],
                        "sample_count": bandit.arm_counts[i] if i < len(bandit.arm_counts) else 0,
                        "allocation_probability": config.initial_allocation.get(variant, 0.0),
                    }
        
        return performance
    
    async def get_allocation_stats(self) -> Dict[str, Any]:
        """Get comprehensive allocation engine statistics."""
        
        return {
            "allocation_stats": self.allocation_stats.copy(),
            "active_experiments": len(self.active_experiments),
            "total_variants": sum(
                len(config.initial_allocation) 
                for config in self.active_experiments.values()
            ),
            "adaptive_experiments": len(self.bandit_optimizers),
            "update_task_running": self.update_task and not self.update_task.done(),
            "allocation_history_size": len(self.allocation_history),
        }


# Global allocation engine instance
_allocation_engine: Optional[AdaptiveAllocationEngine] = None


def get_allocation_engine() -> AdaptiveAllocationEngine:
    """Get global allocation engine instance."""
    global _allocation_engine
    
    if _allocation_engine is None:
        _allocation_engine = AdaptiveAllocationEngine()
    
    return _allocation_engine


__all__ = [
    "AdaptiveAllocationEngine",
    "AllocationStrategy",
    "AllocationConfig", 
    "AllocationDecision",
    "get_allocation_engine",
]