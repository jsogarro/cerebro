"""
Unified Experiment Manager for System-Wide A/B Testing

This module extends the existing PromptVersionManager to support system-wide
experimentation across all Cerebro intelligence components including MASR routing,
Agent APIs, Memory systems, and TalkHier protocols.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from datetime import datetime, timedelta
import uuid
import asyncio
import json
import hashlib
from abc import ABC, abstractmethod

# Import existing prompt version manager for extension
from src.ai_brain.prompts.prompt_version_manager import PromptVersionManager


class ExperimentType(Enum):
    """Types of experiments supported by the system."""
    AB_TEST = "ab_test"
    MULTI_ARMED_BANDIT = "multi_armed_bandit"
    BAYESIAN_OPTIMIZATION = "bayesian_optimization"
    CONTEXTUAL_BANDIT = "contextual_bandit"
    SEQUENTIAL_TEST = "sequential_test"


class ExperimentStatus(Enum):
    """Experiment lifecycle states."""
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED_SAFETY = "stopped_safety"
    WINNER_PROMOTED = "winner_promoted"


class SystemComponent(Enum):
    """System components that can be experimented on."""
    PROMPTS = "prompts"
    MASR_ROUTING = "masr_routing"
    AGENT_APIS = "agent_apis"
    MEMORY_SYSTEM = "memory_system"
    TALKHIER_PROTOCOL = "talkhier_protocol"
    EXECUTION_PATTERNS = "execution_patterns"
    SUPERVISOR_ALLOCATION = "supervisor_allocation"


@dataclass
class ExperimentVariant:
    """Represents a single variant in an experiment."""
    id: str
    name: str
    description: str
    allocation: float  # Percentage of traffic (0.0 to 1.0)
    configuration: Dict[str, Any]
    is_control: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentMetrics:
    """Metrics tracked for an experiment."""
    primary_metrics: List[str]
    secondary_metrics: List[str]
    guardrail_metrics: List[str]  # Metrics that trigger safety stops
    custom_metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StatisticalConfig:
    """Statistical configuration for an experiment."""
    confidence_level: float = 0.95
    minimum_sample_size: int = 1000
    power: float = 0.8
    effect_size: float = 0.1
    multiple_comparison_correction: str = "bonferroni"
    early_stopping_enabled: bool = True
    sequential_testing: bool = False


@dataclass
class SuccessCriteria:
    """Success criteria for experiment completion."""
    metric: str
    improvement_threshold: float  # Minimum improvement required
    confidence_threshold: float = 0.95
    practical_significance: float = 0.05  # ROPE threshold


@dataclass
class SystemExperiment:
    """Complete experiment definition for system-wide testing."""
    id: str
    name: str
    description: str
    type: ExperimentType
    components: List[SystemComponent]
    variants: List[ExperimentVariant]
    metrics: ExperimentMetrics
    statistical_config: StatisticalConfig
    success_criteria: List[SuccessCriteria]
    status: ExperimentStatus
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    owner: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExperimentAllocationStrategy(ABC):
    """Abstract base class for experiment allocation strategies."""
    
    @abstractmethod
    async def get_variant(self, 
                          experiment: SystemExperiment,
                          context: Dict[str, Any]) -> ExperimentVariant:
        """Determine which variant to assign based on context."""
        pass
    
    @abstractmethod
    async def update_allocation(self,
                               experiment: SystemExperiment,
                               performance_data: Dict[str, Any]):
        """Update allocation based on performance (for adaptive strategies)."""
        pass


class RandomAllocationStrategy(ExperimentAllocationStrategy):
    """Simple random allocation based on variant percentages."""
    
    async def get_variant(self,
                          experiment: SystemExperiment,
                          context: Dict[str, Any]) -> ExperimentVariant:
        """Randomly assign variant based on allocation percentages."""
        import random
        
        # Create cumulative distribution
        cumulative = 0.0
        rand = random.random()
        
        for variant in experiment.variants:
            cumulative += variant.allocation
            if rand < cumulative:
                return variant
        
        # Fallback to last variant (shouldn't happen with proper allocations)
        return experiment.variants[-1]
    
    async def update_allocation(self,
                               experiment: SystemExperiment,
                               performance_data: Dict[str, Any]):
        """No updates for random allocation."""
        pass


class DeterministicAllocationStrategy(ExperimentAllocationStrategy):
    """Deterministic allocation based on user/session hash."""
    
    def _hash_context(self, context: Dict[str, Any], experiment_id: str) -> float:
        """Create deterministic hash from context."""
        # Use user_id or session_id for consistent assignment
        identifier = context.get('user_id') or context.get('session_id', '')
        hash_input = f"{experiment_id}:{identifier}"
        hash_value = hashlib.md5(hash_input.encode()).hexdigest()
        # Convert to float between 0 and 1
        return int(hash_value, 16) / (16 ** len(hash_value))
    
    async def get_variant(self,
                          experiment: SystemExperiment,
                          context: Dict[str, Any]) -> ExperimentVariant:
        """Deterministically assign variant based on context hash."""
        hash_value = self._hash_context(context, experiment.id)
        
        cumulative = 0.0
        for variant in experiment.variants:
            cumulative += variant.allocation
            if hash_value < cumulative:
                return variant
        
        return experiment.variants[-1]
    
    async def update_allocation(self,
                               experiment: SystemExperiment,
                               performance_data: Dict[str, Any]):
        """No updates for deterministic allocation."""
        pass


class UnifiedExperimentManager:
    """
    Manages system-wide experiments across all Cerebro components.
    Extends PromptVersionManager functionality to entire system.
    """
    
    def __init__(self, 
                 prompt_manager: Optional[PromptVersionManager] = None,
                 allocation_strategy: Optional[ExperimentAllocationStrategy] = None):
        """
        Initialize the unified experiment manager.
        
        Args:
            prompt_manager: Existing prompt version manager for backward compatibility
            allocation_strategy: Strategy for allocating users to variants
        """
        self.prompt_manager = prompt_manager
        self.allocation_strategy = allocation_strategy or DeterministicAllocationStrategy()
        self.active_experiments: Dict[str, SystemExperiment] = {}
        self.experiment_history: List[SystemExperiment] = []
        self.assignment_cache: Dict[str, Dict[str, ExperimentVariant]] = {}
        
    async def create_experiment(self, 
                               experiment_config: Dict[str, Any]) -> SystemExperiment:
        """
        Create a new system-wide experiment.
        
        Args:
            experiment_config: Configuration dictionary for the experiment
            
        Returns:
            Created SystemExperiment instance
        """
        # Parse configuration
        experiment = SystemExperiment(
            id=str(uuid.uuid4()),
            name=experiment_config['name'],
            description=experiment_config.get('description', ''),
            type=ExperimentType(experiment_config['type']),
            components=[SystemComponent(c) for c in experiment_config['components']],
            variants=[self._parse_variant(v) for v in experiment_config['variants']],
            metrics=self._parse_metrics(experiment_config['metrics']),
            statistical_config=self._parse_statistical_config(
                experiment_config.get('statistical_config', {})
            ),
            success_criteria=[
                self._parse_success_criteria(c) 
                for c in experiment_config.get('success_criteria', [])
            ],
            status=ExperimentStatus.CREATED,
            owner=experiment_config.get('owner'),
            metadata=experiment_config.get('metadata', {})
        )
        
        # Validate experiment configuration
        self._validate_experiment(experiment)
        
        # Store experiment
        self.active_experiments[experiment.id] = experiment
        
        return experiment
    
    async def start_experiment(self, experiment_id: str):
        """Start running an experiment."""
        if experiment_id not in self.active_experiments:
            raise ValueError(f"Experiment {experiment_id} not found")
        
        experiment = self.active_experiments[experiment_id]
        experiment.status = ExperimentStatus.RUNNING
        experiment.start_time = datetime.utcnow()
        
        # Initialize tracking for each component
        await self._initialize_component_tracking(experiment)
    
    async def get_variant_for_context(self,
                                     experiment_id: str,
                                     context: Dict[str, Any]) -> Optional[ExperimentVariant]:
        """
        Get the appropriate variant for a given context.
        
        Args:
            experiment_id: ID of the experiment
            context: Context information (user_id, session_id, query features, etc.)
            
        Returns:
            Assigned ExperimentVariant or None if experiment not active
        """
        if experiment_id not in self.active_experiments:
            return None
        
        experiment = self.active_experiments[experiment_id]
        
        if experiment.status != ExperimentStatus.RUNNING:
            return None
        
        # Check cache first
        cache_key = self._get_cache_key(experiment_id, context)
        if cache_key in self.assignment_cache.get(experiment_id, {}):
            return self.assignment_cache[experiment_id][cache_key]
        
        # Get variant from allocation strategy
        variant = await self.allocation_strategy.get_variant(experiment, context)
        
        # Cache assignment
        if experiment_id not in self.assignment_cache:
            self.assignment_cache[experiment_id] = {}
        self.assignment_cache[experiment_id][cache_key] = variant
        
        return variant
    
    async def track_assignment(self,
                              experiment_id: str,
                              context: Dict[str, Any],
                              variant: ExperimentVariant):
        """Track that a user/session was assigned to a variant."""
        # This would integrate with the metrics collection system
        # For now, we'll just log it
        assignment_event = {
            'experiment_id': experiment_id,
            'variant_id': variant.id,
            'timestamp': datetime.utcnow().isoformat(),
            'context': context
        }
        # TODO: Send to metrics pipeline
        
    async def track_metric(self,
                          experiment_id: str,
                          variant_id: str,
                          metric_name: str,
                          value: float,
                          context: Optional[Dict[str, Any]] = None):
        """Track a metric value for an experiment variant."""
        metric_event = {
            'experiment_id': experiment_id,
            'variant_id': variant_id,
            'metric_name': metric_name,
            'value': value,
            'timestamp': datetime.utcnow().isoformat(),
            'context': context or {}
        }
        # TODO: Send to metrics pipeline
    
    async def check_experiment_status(self, experiment_id: str) -> ExperimentStatus:
        """
        Check current status of an experiment and update if needed.
        
        This includes checking for:
        - Statistical significance reached
        - Safety thresholds violated
        - Minimum sample size reached
        - Time limits exceeded
        """
        if experiment_id not in self.active_experiments:
            raise ValueError(f"Experiment {experiment_id} not found")
        
        experiment = self.active_experiments[experiment_id]
        
        # TODO: Integrate with statistical analysis engine
        # For now, return current status
        return experiment.status
    
    async def stop_experiment(self,
                            experiment_id: str,
                            reason: str = "manual_stop"):
        """Stop a running experiment."""
        if experiment_id not in self.active_experiments:
            raise ValueError(f"Experiment {experiment_id} not found")
        
        experiment = self.active_experiments[experiment_id]
        
        if reason == "safety":
            experiment.status = ExperimentStatus.STOPPED_SAFETY
        else:
            experiment.status = ExperimentStatus.COMPLETED
        
        experiment.end_time = datetime.utcnow()
        
        # Move to history
        self.experiment_history.append(experiment)
        del self.active_experiments[experiment_id]
        
        # Clear cache
        if experiment_id in self.assignment_cache:
            del self.assignment_cache[experiment_id]
    
    async def promote_winner(self,
                           experiment_id: str,
                           winning_variant_id: str):
        """Promote the winning variant to production."""
        # Find experiment in history
        experiment = None
        for exp in self.experiment_history:
            if exp.id == experiment_id:
                experiment = exp
                break
        
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found in history")
        
        # Find winning variant
        winning_variant = None
        for variant in experiment.variants:
            if variant.id == winning_variant_id:
                winning_variant = variant
                break
        
        if not winning_variant:
            raise ValueError(f"Variant {winning_variant_id} not found")
        
        # Apply winning configuration to appropriate components
        await self._apply_variant_configuration(experiment, winning_variant)
        
        experiment.status = ExperimentStatus.WINNER_PROMOTED
    
    # Helper methods
    
    def _parse_variant(self, variant_config: Dict[str, Any]) -> ExperimentVariant:
        """Parse variant configuration."""
        return ExperimentVariant(
            id=variant_config.get('id', str(uuid.uuid4())),
            name=variant_config['name'],
            description=variant_config.get('description', ''),
            allocation=variant_config['allocation'],
            configuration=variant_config['configuration'],
            is_control=variant_config.get('is_control', False),
            metadata=variant_config.get('metadata', {})
        )
    
    def _parse_metrics(self, metrics_config: Dict[str, Any]) -> ExperimentMetrics:
        """Parse metrics configuration."""
        return ExperimentMetrics(
            primary_metrics=metrics_config.get('primary', []),
            secondary_metrics=metrics_config.get('secondary', []),
            guardrail_metrics=metrics_config.get('guardrail', []),
            custom_metrics=metrics_config.get('custom', {})
        )
    
    def _parse_statistical_config(self, config: Dict[str, Any]) -> StatisticalConfig:
        """Parse statistical configuration."""
        return StatisticalConfig(
            confidence_level=config.get('confidence_level', 0.95),
            minimum_sample_size=config.get('minimum_sample_size', 1000),
            power=config.get('power', 0.8),
            effect_size=config.get('effect_size', 0.1),
            multiple_comparison_correction=config.get('correction', 'bonferroni'),
            early_stopping_enabled=config.get('early_stopping', True),
            sequential_testing=config.get('sequential', False)
        )
    
    def _parse_success_criteria(self, criteria_config: Dict[str, Any]) -> SuccessCriteria:
        """Parse success criteria configuration."""
        return SuccessCriteria(
            metric=criteria_config['metric'],
            improvement_threshold=criteria_config['improvement'],
            confidence_threshold=criteria_config.get('confidence', 0.95),
            practical_significance=criteria_config.get('practical_significance', 0.05)
        )
    
    def _validate_experiment(self, experiment: SystemExperiment):
        """Validate experiment configuration."""
        # Check allocations sum to 1.0
        total_allocation = sum(v.allocation for v in experiment.variants)
        if abs(total_allocation - 1.0) > 0.001:
            raise ValueError(f"Variant allocations must sum to 1.0, got {total_allocation}")
        
        # Ensure at least one control variant
        has_control = any(v.is_control for v in experiment.variants)
        if not has_control:
            # Mark first variant as control
            experiment.variants[0].is_control = True
        
        # Validate metrics exist
        if not experiment.metrics.primary_metrics:
            raise ValueError("At least one primary metric is required")
    
    def _get_cache_key(self, experiment_id: str, context: Dict[str, Any]) -> str:
        """Generate cache key for assignment."""
        identifier = context.get('user_id') or context.get('session_id', 'unknown')
        return f"{experiment_id}:{identifier}"
    
    async def _initialize_component_tracking(self, experiment: SystemExperiment):
        """Initialize tracking for experiment components."""
        # This would set up hooks in each component system
        # For now, placeholder implementation
        for component in experiment.components:
            if component == SystemComponent.MASR_ROUTING:
                # Register with MASR router
                pass
            elif component == SystemComponent.AGENT_APIS:
                # Register with Agent API system
                pass
            elif component == SystemComponent.MEMORY_SYSTEM:
                # Register with Memory system
                pass
            # ... etc
    
    async def _apply_variant_configuration(self,
                                         experiment: SystemExperiment,
                                         variant: ExperimentVariant):
        """Apply winning variant configuration to production."""
        for component in experiment.components:
            if component == SystemComponent.MASR_ROUTING:
                # Update MASR routing configuration
                pass
            elif component == SystemComponent.AGENT_APIS:
                # Update Agent API configuration
                pass
            elif component == SystemComponent.MEMORY_SYSTEM:
                # Update Memory system configuration
                pass
            # ... etc
    
    async def get_active_experiments(self) -> List[SystemExperiment]:
        """Get all currently active experiments."""
        return list(self.active_experiments.values())
    
    async def get_experiment(self, experiment_id: str) -> Optional[SystemExperiment]:
        """Get a specific experiment by ID."""
        return self.active_experiments.get(experiment_id)