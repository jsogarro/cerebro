"""
Supervisor Configuration Module

Configuration translation and management for supervisor-based execution.
Translates MASR complexity analysis and routing strategies into supervisor
configuration parameters for optimal performance and quality.

Key Features:
- Complexity-to-worker allocation mapping
- Quality threshold calculation based on routing strategies
- Refinement round calculation from uncertainty levels
- Collaboration mode to supervision mode translation
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...agents.supervisors.base_supervisor import SupervisionMode, WorkerAllocation
from ..router.masr import CollaborationMode, RoutingStrategy
from ..router.query_analyzer import ComplexityLevel

logger = logging.getLogger(__name__)


class QualityFocusLevel(Enum):
    """Quality focus levels for supervisor configuration."""
    
    MINIMAL = "minimal"      # Speed-optimized, basic quality
    STANDARD = "standard"    # Balanced approach
    HIGH = "high"           # Quality-focused with extended validation
    MAXIMUM = "maximum"     # Maximum quality, comprehensive validation


@dataclass
class SupervisorConfigurationProfile:
    """Configuration profile for supervisor execution."""
    
    # Worker allocation strategy
    worker_allocation_strategy: WorkerAllocation = WorkerAllocation.OPTIMAL_SET
    min_workers: int = 1
    max_workers: int = 8
    optimal_worker_count: int = 3
    
    # Quality and refinement settings
    quality_threshold: float = 0.85
    consensus_threshold: float = 0.90
    max_refinement_rounds: int = 3
    early_stopping_enabled: bool = True
    
    # Timing and resource constraints
    timeout_seconds: int = 300
    max_parallel_workers: int = 5
    memory_limit_mb: int = 1024
    
    # Supervision mode configuration
    supervision_mode: SupervisionMode = SupervisionMode.PARALLEL
    enable_cross_validation: bool = True
    enable_consensus_building: bool = True
    
    # Adaptive behavior
    enable_adaptive_allocation: bool = True
    complexity_scaling_factor: float = 1.2
    uncertainty_penalty_factor: float = 0.15
    
    # Context preservation
    preserve_intermediate_results: bool = True
    enable_detailed_logging: bool = False
    
    # Performance optimization
    enable_result_caching: bool = True
    cache_ttl_seconds: int = 3600
    enable_parallel_validation: bool = True


class ComplexityToWorkerMapper:
    """Maps complexity analysis to optimal worker allocation."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize complexity mapper."""
        self.config = config or {}
        
        # Base worker allocation by complexity level
        self.complexity_worker_map = {
            ComplexityLevel.SIMPLE: {
                "min_workers": 1,
                "optimal_workers": 2,
                "max_workers": 3,
                "allocation_strategy": WorkerAllocation.MINIMAL_VIABLE,
            },
            ComplexityLevel.MODERATE: {
                "min_workers": 2,
                "optimal_workers": 4,
                "max_workers": 6,
                "allocation_strategy": WorkerAllocation.OPTIMAL_SET,
            },
            ComplexityLevel.COMPLEX: {
                "min_workers": 3,
                "optimal_workers": 6,
                "max_workers": 10,
                "allocation_strategy": WorkerAllocation.ALL_WORKERS,
            },
        }
        
        # Domain-specific adjustments
        self.domain_adjustments = {
            "research": {"worker_multiplier": 1.2, "quality_bonus": 0.05},
            "content": {"worker_multiplier": 1.0, "quality_bonus": 0.0},
            "analytics": {"worker_multiplier": 1.3, "quality_bonus": 0.08},
            "service": {"worker_multiplier": 0.8, "quality_bonus": -0.02},
        }
    
    def calculate_worker_allocation(
        self, 
        complexity_level: ComplexityLevel,
        domain: str,
        subtask_count: int,
        uncertainty: float
    ) -> dict[str, Any]:
        """
        Calculate optimal worker allocation.
        
        Args:
            complexity_level: Query complexity level
            domain: Primary domain for the task
            subtask_count: Number of identified subtasks
            uncertainty: Uncertainty score from complexity analysis
            
        Returns:
            Worker allocation configuration
        """
        
        # Get base allocation for complexity level
        base_allocation = self.complexity_worker_map.get(
            complexity_level, 
            self.complexity_worker_map[ComplexityLevel.MODERATE]
        )
        
        # Apply domain adjustments
        domain_adj = self.domain_adjustments.get(domain, {"worker_multiplier": 1.0, "quality_bonus": 0.0})
        worker_multiplier: float = domain_adj.get("worker_multiplier", 1.0)

        # Calculate worker counts
        base_min = base_allocation.get("min_workers", 1)
        base_optimal = base_allocation.get("optimal_workers", 3)
        base_max = base_allocation.get("max_workers", 5)
        min_workers = max(1, int(base_min * worker_multiplier))
        optimal_workers = int(base_optimal * worker_multiplier)
        max_workers = int(base_max * worker_multiplier)
        
        # Adjust based on subtask count
        if subtask_count > 0:
            # Scale workers based on subtasks, but cap the scaling
            subtask_scaling = min(subtask_count / 3.0, 2.0)  # Cap at 2x scaling
            optimal_workers = int(optimal_workers * subtask_scaling)
            max_workers = int(max_workers * subtask_scaling)
        
        # Adjust for uncertainty (higher uncertainty = more workers for validation)
        if uncertainty > 0.7:
            uncertainty_scaling = 1.0 + (uncertainty - 0.7) * 0.5
            optimal_workers = int(optimal_workers * uncertainty_scaling)
            max_workers = int(max_workers * uncertainty_scaling)
        
        # Final bounds checking
        min_workers = max(1, min_workers)
        optimal_workers = max(min_workers, min(optimal_workers, 12))  # Hard cap at 12
        max_workers = max(optimal_workers, min(max_workers, 15))      # Hard cap at 15
        
        return {
            "min_workers": min_workers,
            "optimal_workers": optimal_workers,
            "max_workers": max_workers,
            "allocation_strategy": base_allocation["allocation_strategy"],
            "domain_multiplier": worker_multiplier,
            "uncertainty_adjustment": uncertainty if uncertainty > 0.7 else 0.0,
        }


class QualityThresholdCalculator:
    """Calculates quality thresholds based on routing strategies."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize quality threshold calculator."""
        self.config = config or {}
        
        # Base quality thresholds by routing strategy
        self.strategy_quality_map = {
            RoutingStrategy.SPEED_FIRST: {
                "quality_threshold": 0.75,
                "consensus_threshold": 0.80,
                "quality_focus": QualityFocusLevel.MINIMAL,
            },
            RoutingStrategy.COST_EFFICIENT: {
                "quality_threshold": 0.80,
                "consensus_threshold": 0.85,
                "quality_focus": QualityFocusLevel.STANDARD,
            },
            RoutingStrategy.QUALITY_FOCUSED: {
                "quality_threshold": 0.95,
                "consensus_threshold": 0.95,
                "quality_focus": QualityFocusLevel.MAXIMUM,
            },
            RoutingStrategy.BALANCED: {
                "quality_threshold": 0.85,
                "consensus_threshold": 0.88,
                "quality_focus": QualityFocusLevel.STANDARD,
            },
            RoutingStrategy.ADAPTIVE: {
                "quality_threshold": 0.85,
                "consensus_threshold": 0.88,
                "quality_focus": QualityFocusLevel.HIGH,
            },
        }
        
        # Complexity-based adjustments
        self.complexity_adjustments = {
            ComplexityLevel.SIMPLE: {"threshold_adjustment": -0.05},
            ComplexityLevel.MODERATE: {"threshold_adjustment": 0.0},
            ComplexityLevel.COMPLEX: {"threshold_adjustment": 0.05},
        }
    
    def calculate_quality_thresholds(
        self,
        routing_strategy: RoutingStrategy,
        complexity_level: ComplexityLevel,
        uncertainty: float,
        priority_level: str = "normal"
    ) -> dict[str, Any]:
        """
        Calculate quality thresholds for supervisor configuration.
        
        Args:
            routing_strategy: MASR routing strategy
            complexity_level: Query complexity level
            uncertainty: Uncertainty score
            priority_level: Priority level (critical, high, normal, low)
            
        Returns:
            Quality threshold configuration
        """
        
        # Get base thresholds for strategy
        base_config = self.strategy_quality_map.get(
            routing_strategy,
            self.strategy_quality_map[RoutingStrategy.BALANCED]
        )
        
        quality_threshold: float = base_config.get("quality_threshold", 0.8)
        consensus_threshold: float = base_config.get("consensus_threshold", 0.85)
        quality_focus = base_config.get("quality_focus", QualityFocusLevel.STANDARD.value)

        # Apply complexity adjustments
        complexity_adj = self.complexity_adjustments.get(
            complexity_level, {"threshold_adjustment": 0.0}
        )
        threshold_adj: float = complexity_adj.get("threshold_adjustment", 0.0)
        quality_threshold = quality_threshold + threshold_adj
        consensus_threshold = consensus_threshold + threshold_adj
        
        # Adjust for uncertainty (higher uncertainty = higher quality requirements)
        if uncertainty > 0.6:
            uncertainty_adjustment = (uncertainty - 0.6) * 0.1
            quality_threshold += uncertainty_adjustment
            consensus_threshold += uncertainty_adjustment
        
        # Apply priority adjustments
        priority_adjustments = {
            "critical": 0.1,
            "high": 0.05,
            "normal": 0.0,
            "low": -0.05,
        }
        
        priority_adj = priority_adjustments.get(priority_level, 0.0)
        quality_threshold += priority_adj
        consensus_threshold += priority_adj
        
        # Ensure thresholds are within valid bounds
        quality_threshold = max(0.6, min(0.98, quality_threshold))
        consensus_threshold = max(0.65, min(0.98, consensus_threshold))
        
        return {
            "quality_threshold": quality_threshold,
            "consensus_threshold": consensus_threshold,
            "quality_focus": quality_focus,
            "base_strategy": routing_strategy.value,
            "complexity_adjustment": complexity_adj["threshold_adjustment"],
            "uncertainty_adjustment": uncertainty if uncertainty > 0.6 else 0.0,
            "priority_adjustment": priority_adj,
        }


class RefinementRoundCalculator:
    """Calculates refinement rounds based on uncertainty and quality requirements."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize refinement round calculator."""
        self.config = config or {}
        
        # Base refinement rounds by quality focus
        self.quality_focus_rounds = {
            QualityFocusLevel.MINIMAL: {"base_rounds": 1, "max_rounds": 2},
            QualityFocusLevel.STANDARD: {"base_rounds": 2, "max_rounds": 3},
            QualityFocusLevel.HIGH: {"base_rounds": 3, "max_rounds": 4},
            QualityFocusLevel.MAXIMUM: {"base_rounds": 3, "max_rounds": 5},
        }
    
    def calculate_refinement_rounds(
        self,
        quality_focus: QualityFocusLevel,
        uncertainty: float,
        complexity_level: ComplexityLevel,
        enable_early_stopping: bool = True
    ) -> dict[str, Any]:
        """
        Calculate optimal refinement rounds configuration.
        
        Args:
            quality_focus: Quality focus level
            uncertainty: Uncertainty score from analysis
            complexity_level: Query complexity level
            enable_early_stopping: Whether to enable early stopping
            
        Returns:
            Refinement rounds configuration
        """
        
        # Get base rounds for quality focus
        focus_config = self.quality_focus_rounds.get(
            quality_focus,
            self.quality_focus_rounds[QualityFocusLevel.STANDARD]
        )
        
        base_rounds = focus_config["base_rounds"]
        max_rounds = focus_config["max_rounds"]
        
        # Adjust based on uncertainty
        if uncertainty > 0.8:
            # High uncertainty requires more refinement
            base_rounds += 1
            max_rounds += 1
        elif uncertainty < 0.3:
            # Low uncertainty can use fewer rounds
            base_rounds = max(1, base_rounds - 1)
        
        # Adjust based on complexity
        complexity_adjustments = {
            ComplexityLevel.SIMPLE: {"rounds_adjustment": -1},
            ComplexityLevel.MODERATE: {"rounds_adjustment": 0},
            ComplexityLevel.COMPLEX: {"rounds_adjustment": 1},
        }
        
        rounds_adj = complexity_adjustments.get(
            complexity_level, {"rounds_adjustment": 0}
        )["rounds_adjustment"]
        
        base_rounds += rounds_adj
        max_rounds += rounds_adj
        
        # Ensure valid bounds
        base_rounds = max(1, min(base_rounds, 4))
        max_rounds = max(base_rounds, min(max_rounds, 6))
        
        # Calculate early stopping threshold
        early_stopping_threshold = 0.95 if enable_early_stopping else 1.0
        
        return {
            "base_refinement_rounds": base_rounds,
            "max_refinement_rounds": max_rounds,
            "enable_early_stopping": enable_early_stopping,
            "early_stopping_threshold": early_stopping_threshold,
            "uncertainty_factor": uncertainty,
            "complexity_adjustment": rounds_adj,
        }


class CollaborationModeTranslator:
    """Translates MASR collaboration modes to supervision modes."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize collaboration mode translator."""
        self.config = config or {}
        
        # Collaboration mode to supervision mode mapping
        self.collaboration_to_supervision = {
            CollaborationMode.DIRECT: SupervisionMode.SEQUENTIAL,
            CollaborationMode.PARALLEL: SupervisionMode.PARALLEL,
            CollaborationMode.HIERARCHICAL: SupervisionMode.HYBRID,
            CollaborationMode.DEBATE: SupervisionMode.ADAPTIVE,
            CollaborationMode.ENSEMBLE: SupervisionMode.PARALLEL,
        }
        
        # Additional configuration by collaboration mode
        self.collaboration_configs = {
            CollaborationMode.DIRECT: {
                "enable_cross_validation": False,
                "max_parallel_workers": 1,
                "consensus_building": False,
            },
            CollaborationMode.PARALLEL: {
                "enable_cross_validation": True,
                "max_parallel_workers": 5,
                "consensus_building": True,
            },
            CollaborationMode.HIERARCHICAL: {
                "enable_cross_validation": True,
                "max_parallel_workers": 3,
                "consensus_building": True,
            },
            CollaborationMode.DEBATE: {
                "enable_cross_validation": True,
                "max_parallel_workers": 3,
                "consensus_building": True,
            },
            CollaborationMode.ENSEMBLE: {
                "enable_cross_validation": True,
                "max_parallel_workers": 5,
                "consensus_building": True,
            },
        }
    
    def translate_collaboration_mode(
        self, collaboration_mode: CollaborationMode
    ) -> dict[str, Any]:
        """
        Translate collaboration mode to supervision configuration.
        
        Args:
            collaboration_mode: MASR collaboration mode
            
        Returns:
            Supervision mode configuration
        """
        
        supervision_mode = self.collaboration_to_supervision.get(
            collaboration_mode, SupervisionMode.PARALLEL
        )
        
        additional_config = self.collaboration_configs.get(
            collaboration_mode, self.collaboration_configs[CollaborationMode.PARALLEL]
        )
        
        return {
            "supervision_mode": supervision_mode,
            "collaboration_mode": collaboration_mode.value,
            **additional_config
        }


class SupervisorConfigurationManager:
    """
    Main configuration manager that integrates all configuration components.
    
    Provides a unified interface for translating MASR routing decisions and
    complexity analysis into comprehensive supervisor configurations.
    """
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize supervisor configuration manager."""
        self.config = config or {}
        
        # Initialize component calculators
        self.worker_mapper = ComplexityToWorkerMapper(
            self.config.get("worker_mapper", {})
        )
        self.quality_calculator = QualityThresholdCalculator(
            self.config.get("quality_calculator", {})
        )
        self.refinement_calculator = RefinementRoundCalculator(
            self.config.get("refinement_calculator", {})
        )
        self.collaboration_translator = CollaborationModeTranslator(
            self.config.get("collaboration_translator", {})
        )
    
    def create_supervisor_configuration(
        self,
        complexity_level: ComplexityLevel,
        domains: list[str],
        subtask_count: int,
        uncertainty: float,
        routing_strategy: RoutingStrategy,
        collaboration_mode: CollaborationMode,
        priority_level: str = "normal",
        additional_context: dict[str, Any] | None = None
    ) -> SupervisorConfigurationProfile:
        """
        Create comprehensive supervisor configuration.
        
        Args:
            complexity_level: Query complexity level
            domains: Identified domains
            subtask_count: Number of subtasks
            uncertainty: Uncertainty score
            routing_strategy: MASR routing strategy
            collaboration_mode: MASR collaboration mode
            priority_level: Priority level
            additional_context: Additional context data
            
        Returns:
            Complete supervisor configuration profile
        """
        
        primary_domain = domains[0] if domains else "research"
        context = additional_context or {}
        
        # Calculate worker allocation
        worker_config = self.worker_mapper.calculate_worker_allocation(
            complexity_level, primary_domain, subtask_count, uncertainty
        )
        
        # Calculate quality thresholds
        quality_config = self.quality_calculator.calculate_quality_thresholds(
            routing_strategy, complexity_level, uncertainty, priority_level
        )
        
        # Calculate refinement rounds
        refinement_config = self.refinement_calculator.calculate_refinement_rounds(
            quality_config["quality_focus"],
            uncertainty,
            complexity_level,
            enable_early_stopping=True
        )
        
        # Translate collaboration mode
        supervision_config = self.collaboration_translator.translate_collaboration_mode(
            collaboration_mode
        )
        
        # Calculate timeout based on complexity and worker count
        base_timeout = {
            ComplexityLevel.SIMPLE: 120,
            ComplexityLevel.MODERATE: 300,
            ComplexityLevel.COMPLEX: 600,
        }.get(complexity_level, 300)
        
        # Scale timeout by worker count and refinement rounds
        timeout_scaling = (
            worker_config["optimal_workers"] * 0.3 +
            refinement_config["max_refinement_rounds"] * 0.5
        )
        timeout_seconds = int(base_timeout * (1 + timeout_scaling))
        
        # Create configuration profile
        profile = SupervisorConfigurationProfile(
            # Worker allocation
            worker_allocation_strategy=worker_config["allocation_strategy"],
            min_workers=worker_config["min_workers"],
            max_workers=worker_config["max_workers"],
            optimal_worker_count=worker_config["optimal_workers"],
            
            # Quality thresholds
            quality_threshold=quality_config["quality_threshold"],
            consensus_threshold=quality_config["consensus_threshold"],
            
            # Refinement settings
            max_refinement_rounds=refinement_config["max_refinement_rounds"],
            early_stopping_enabled=refinement_config["enable_early_stopping"],
            
            # Supervision mode
            supervision_mode=supervision_config["supervision_mode"],
            enable_cross_validation=supervision_config["enable_cross_validation"],
            enable_consensus_building=supervision_config["consensus_building"],
            
            # Timing and resources
            timeout_seconds=timeout_seconds,
            max_parallel_workers=min(
                supervision_config["max_parallel_workers"],
                worker_config["optimal_workers"]
            ),
            
            # Adaptive behavior based on uncertainty
            enable_adaptive_allocation=uncertainty > 0.5,
            uncertainty_penalty_factor=uncertainty * 0.2,
            
            # Performance settings based on routing strategy
            enable_result_caching=routing_strategy != RoutingStrategy.SPEED_FIRST,
            enable_detailed_logging=quality_config["quality_focus"] in [
                QualityFocusLevel.HIGH, QualityFocusLevel.MAXIMUM
            ],
        )
        
        return profile
    
    async def get_configuration_stats(self) -> dict[str, Any]:
        """Get configuration manager statistics."""
        return {
            "components": {
                "worker_mapper": "active",
                "quality_calculator": "active",
                "refinement_calculator": "active", 
                "collaboration_translator": "active",
            },
            "supported_strategies": [s.value for s in RoutingStrategy],
            "supported_collaboration_modes": [c.value for c in CollaborationMode],
            "supported_complexity_levels": [c.value for c in ComplexityLevel],
        }


__all__ = [
    "CollaborationModeTranslator",
    "ComplexityToWorkerMapper",
    "QualityFocusLevel",
    "QualityThresholdCalculator",
    "RefinementRoundCalculator",
    "SupervisorConfigurationManager",
    "SupervisorConfigurationProfile",
]