"""
Supervisor Factory

Dynamic supervisor creation and management system that integrates with MASR
routing decisions to instantiate and configure appropriate supervisors.

Features:
- Dynamic supervisor registration and discovery
- Capability-based supervisor selection
- Health monitoring and failover mechanisms
- Resource management and load balancing
- Integration with MASR routing decisions
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.core.types import FactoryStatsDict, HealthCheckDict, HealthReportDict

from ...ai_brain.integration.masr_supervisor_bridge import SupervisorConfiguration
from ..models import AgentTask
from .base_supervisor import BaseSupervisor
from .research_supervisor import ResearchSupervisor

logger = logging.getLogger(__name__)


class SupervisorCapability(Enum):
    """Supervisor capabilities for matching with requirements."""
    
    # Domain capabilities
    RESEARCH = "research"
    CONTENT = "content"
    ANALYTICS = "analytics"
    SERVICE = "service"
    MULTIMODAL = "multimodal"
    
    # Functional capabilities
    LITERATURE_REVIEW = "literature_review"
    DATA_ANALYSIS = "data_analysis" 
    CONTENT_CREATION = "content_creation"
    QUALITY_ASSURANCE = "quality_assurance"
    CITATION_MANAGEMENT = "citation_management"
    
    # Coordination capabilities
    PARALLEL_COORDINATION = "parallel_coordination"
    SEQUENTIAL_COORDINATION = "sequential_coordination"
    HIERARCHICAL_COORDINATION = "hierarchical_coordination"
    CONSENSUS_BUILDING = "consensus_building"
    
    # Technical capabilities
    TALKHIER_PROTOCOL = "talkhier_protocol"
    LANGGRAPH_WORKFLOWS = "langgraph_workflows"
    MULTI_ROUND_REFINEMENT = "multi_round_refinement"
    CROSS_DOMAIN_SYNTHESIS = "cross_domain_synthesis"


@dataclass
class SupervisorSpecification:
    """Specification for a registered supervisor type."""
    
    supervisor_type: str
    supervisor_class: type[BaseSupervisor]
    domain: str
    capabilities: set[SupervisorCapability]
    
    # Performance characteristics
    average_execution_time_ms: int = 60000  # 1 minute default
    reliability_score: float = 0.95
    quality_score: float = 0.85
    cost_per_execution: float = 0.01
    
    # Resource requirements
    min_workers: int = 1
    max_workers: int = 10
    memory_requirement_mb: int = 512
    
    # Optimization preferences
    optimal_for_complexity: list[str] = field(default_factory=list)  # simple, moderate, complex
    optimal_for_strategies: list[str] = field(default_factory=list)  # speed, cost, quality, balanced
    
    # Health and monitoring
    health_status: str = "healthy"
    last_health_check: datetime = field(default_factory=datetime.now)
    failure_count: int = 0
    success_rate: float = 1.0
    
    # Metadata
    description: str = ""
    version: str = "1.0.0"
    created_at: datetime = field(default_factory=datetime.now)


class SupervisorHealthMonitor:
    """Monitors supervisor health and performance."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize health monitor."""
        self.config = config or {}
        
        # Health check configuration
        self.health_check_interval = self.config.get("health_check_interval_seconds", 300)  # 5 minutes
        self.failure_threshold = self.config.get("failure_threshold", 3)
        self.recovery_time = self.config.get("recovery_time_seconds", 600)  # 10 minutes
        
        # Health data
        self.supervisor_health: dict[str, SupervisorSpecification] = {}
        self.health_history: dict[str, list[dict[str, Any]]] = {}
        
        # Monitoring task
        self._monitoring_task: asyncio.Task[Any] | None = None
        self._monitoring_enabled = True
    
    def register_supervisor(self, spec: SupervisorSpecification) -> None:
        """Register supervisor for health monitoring."""
        self.supervisor_health[spec.supervisor_type] = spec
        self.health_history[spec.supervisor_type] = []

        logger.info(f"Registered supervisor {spec.supervisor_type} for health monitoring")

    def record_execution(self, supervisor_type: str, success: bool, execution_time_ms: int) -> None:
        """Record execution result for health tracking."""
        if supervisor_type not in self.supervisor_health:
            return
        
        spec = self.supervisor_health[supervisor_type]
        
        # Update failure count
        if success:
            spec.failure_count = max(0, spec.failure_count - 1)  # Gradual recovery
        else:
            spec.failure_count += 1
        
        # Update success rate (exponential moving average)
        alpha = 0.1  # Smoothing factor
        if success:
            spec.success_rate = spec.success_rate * (1 - alpha) + alpha
        else:
            spec.success_rate = spec.success_rate * (1 - alpha)
        
        # Update average execution time
        current_avg = spec.average_execution_time_ms
        spec.average_execution_time_ms = int(current_avg * 0.9 + execution_time_ms * 0.1)
        
        # Record health event
        health_event = {
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "execution_time_ms": execution_time_ms,
            "success_rate": spec.success_rate,
            "failure_count": spec.failure_count,
        }
        
        history = self.health_history[supervisor_type]
        history.append(health_event)
        
        # Keep only recent history (last 100 events)
        if len(history) > 100:
            history.pop(0)
        
        # Update health status
        self._update_health_status(spec)
    
    def _update_health_status(self, spec: SupervisorSpecification) -> None:
        """Update supervisor health status."""
        if spec.failure_count >= self.failure_threshold:
            spec.health_status = "unhealthy"
        elif spec.success_rate < 0.8:
            spec.health_status = "degraded"
        elif spec.success_rate < 0.95:
            spec.health_status = "warning"
        else:
            spec.health_status = "healthy"
        
        spec.last_health_check = datetime.now()
    
    def is_supervisor_healthy(self, supervisor_type: str) -> bool:
        """Check if supervisor is healthy for execution."""
        if supervisor_type not in self.supervisor_health:
            return False
        
        spec = self.supervisor_health[supervisor_type]
        
        # Check if in recovery period after failures
        if spec.health_status == "unhealthy":
            time_since_check = datetime.now() - spec.last_health_check
            if time_since_check.total_seconds() < self.recovery_time:
                return False
        
        return spec.health_status in ["healthy", "warning", "degraded"]
    
    def get_healthy_supervisors(self) -> list[str]:
        """Get list of healthy supervisor types."""
        return [
            supervisor_type
            for supervisor_type in self.supervisor_health
            if self.is_supervisor_healthy(supervisor_type)
        ]
    
    async def get_health_report(self) -> HealthReportDict:
        """Get comprehensive health report."""
        supervisor_details: dict[str, dict[str, Any]] = {}

        for supervisor_type, spec in self.supervisor_health.items():
            supervisor_details[supervisor_type] = {
                "health_status": spec.health_status,
                "success_rate": spec.success_rate,
                "failure_count": spec.failure_count,
                "average_execution_time_ms": spec.average_execution_time_ms,
                "last_health_check": spec.last_health_check.isoformat(),
            }

        report: HealthReportDict = {
            "total_supervisors": len(self.supervisor_health),
            "healthy_supervisors": len(self.get_healthy_supervisors()),
            "supervisor_details": supervisor_details,
        }

        return report


class CapabilityMatcher:
    """Matches supervisor capabilities to task requirements."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize capability matcher."""
        self.config = config or {}
        
        # Capability scoring weights
        self.capability_weights = {
            SupervisorCapability.RESEARCH: 1.0,
            SupervisorCapability.CONTENT: 1.0,
            SupervisorCapability.ANALYTICS: 1.0,
            SupervisorCapability.SERVICE: 1.0,
            SupervisorCapability.TALKHIER_PROTOCOL: 0.8,
            SupervisorCapability.LANGGRAPH_WORKFLOWS: 0.8,
            SupervisorCapability.CONSENSUS_BUILDING: 0.7,
            SupervisorCapability.MULTI_ROUND_REFINEMENT: 0.6,
        }
    
    def match_supervisor(
        self,
        requirements: dict[str, Any],
        available_supervisors: list[SupervisorSpecification],
        health_monitor: SupervisorHealthMonitor
    ) -> SupervisorSpecification | None:
        """
        Match supervisor to requirements.
        
        Args:
            requirements: Task requirements from MASR routing
            available_supervisors: List of available supervisor specs
            health_monitor: Health monitor for filtering unhealthy supervisors
            
        Returns:
            Best matching supervisor specification or None
        """
        
        # Filter healthy supervisors
        healthy_supervisors = [
            spec for spec in available_supervisors
            if health_monitor.is_supervisor_healthy(spec.supervisor_type)
        ]
        
        if not healthy_supervisors:
            logger.warning("No healthy supervisors available for matching")
            return None
        
        # Score supervisors based on requirements
        scored_supervisors = []
        
        for spec in healthy_supervisors:
            score = self._calculate_match_score(spec, requirements)
            scored_supervisors.append((score, spec))
        
        # Sort by score (descending)
        scored_supervisors.sort(key=lambda x: x[0], reverse=True)
        
        if scored_supervisors:
            best_score, best_supervisor = scored_supervisors[0]
            logger.info(
                f"Selected supervisor {best_supervisor.supervisor_type} "
                f"with match score {best_score:.3f}"
            )
            return best_supervisor
        
        return None
    
    def _calculate_match_score(
        self, spec: SupervisorSpecification, requirements: dict[str, Any]
    ) -> float:
        """Calculate match score for supervisor against requirements."""
        
        score = 0.0
        total_weight = 0.0
        
        # Domain match (high importance)
        required_domain = requirements.get("domain", "")
        if required_domain == spec.domain:
            score += 2.0
            total_weight += 2.0
        elif not required_domain:  # No specific domain requirement
            score += 1.0
            total_weight += 2.0
        else:
            total_weight += 2.0  # Penalize domain mismatch
        
        # Capability matching
        required_capabilities = set(requirements.get("capabilities", []))
        if required_capabilities:
            capability_overlap = len(required_capabilities & spec.capabilities)
            capability_score = capability_overlap / len(required_capabilities)
            score += capability_score * 1.5
            total_weight += 1.5
        
        # Complexity optimization
        complexity = requirements.get("complexity", "moderate")
        if complexity in spec.optimal_for_complexity:
            score += 1.0
            total_weight += 1.0
        elif spec.optimal_for_complexity:  # Has preferences but doesn't match
            total_weight += 1.0
        
        # Strategy optimization
        strategy = requirements.get("strategy", "balanced")
        if strategy in spec.optimal_for_strategies:
            score += 0.8
            total_weight += 0.8
        elif spec.optimal_for_strategies:  # Has preferences but doesn't match
            total_weight += 0.8
        
        # Performance factors
        performance_score = (
            spec.reliability_score * 0.4 +
            spec.quality_score * 0.4 + 
            spec.success_rate * 0.2
        )
        score += performance_score * 0.8
        total_weight += 0.8
        
        # Worker count compatibility
        required_workers = requirements.get("workers", 3)
        if spec.min_workers <= required_workers <= spec.max_workers:
            score += 0.5
        total_weight += 0.5
        
        # Normalize score
        if total_weight > 0:
            return score / total_weight
        
        return 0.0


class SupervisorFactory:
    """
    Factory for creating and managing supervisor instances.
    
    Integrates with MASR routing decisions to provide intelligent supervisor
    selection and instantiation based on task requirements.
    """
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize supervisor factory."""
        self.config = config or {}
        
        # Core components
        self.health_monitor = SupervisorHealthMonitor(self.config.get("health_monitor", {}))
        self.capability_matcher = CapabilityMatcher(self.config.get("capability_matcher", {}))
        
        # Supervisor registry
        self.supervisor_registry: dict[str, SupervisorSpecification] = {}
        
        # Factory statistics
        self.factory_stats = {
            "total_created": 0,
            "successful_creations": 0,
            "failed_creations": 0,
            "registry_size": 0,
        }
        
        # Initialize with built-in supervisors
        self._register_builtin_supervisors()
    
    def _register_builtin_supervisors(self) -> None:
        """Register built-in supervisor types."""
        
        # Research Supervisor
        research_spec = SupervisorSpecification(
            supervisor_type="research",
            supervisor_class=ResearchSupervisor,
            domain="research",
            capabilities={
                SupervisorCapability.RESEARCH,
                SupervisorCapability.LITERATURE_REVIEW,
                SupervisorCapability.CITATION_MANAGEMENT,
                SupervisorCapability.QUALITY_ASSURANCE,
                SupervisorCapability.TALKHIER_PROTOCOL,
                SupervisorCapability.LANGGRAPH_WORKFLOWS,
                SupervisorCapability.MULTI_ROUND_REFINEMENT,
                SupervisorCapability.CONSENSUS_BUILDING,
                SupervisorCapability.HIERARCHICAL_COORDINATION,
            },
            average_execution_time_ms=90000,  # 1.5 minutes
            reliability_score=0.95,
            quality_score=0.90,
            cost_per_execution=0.015,
            min_workers=3,
            max_workers=8,
            optimal_for_complexity=["moderate", "complex"],
            optimal_for_strategies=["quality_focused", "balanced"],
            description="Coordinates research teams for literature review, analysis, and synthesis",
            version="1.0.0"
        )
        
        self.register_supervisor(research_spec)
    
    def register_supervisor(self, spec: SupervisorSpecification) -> None:
        """
        Register a supervisor type with the factory.
        
        Args:
            spec: Supervisor specification to register
        """
        
        self.supervisor_registry[spec.supervisor_type] = spec
        self.health_monitor.register_supervisor(spec)
        self.factory_stats["registry_size"] = len(self.supervisor_registry)
        
        logger.info(f"Registered supervisor type: {spec.supervisor_type}")
    
    def get_available_supervisors(self) -> list[SupervisorSpecification]:
        """Get list of all available supervisor specifications."""
        return list(self.supervisor_registry.values())
    
    def get_supervisor_spec(self, supervisor_type: str) -> SupervisorSpecification | None:
        """Get specification for specific supervisor type."""
        return self.supervisor_registry.get(supervisor_type)
    
    async def create_supervisor_from_config(
        self, config: SupervisorConfiguration
    ) -> BaseSupervisor | None:
        """
        Create supervisor instance from configuration.
        
        Args:
            config: Supervisor configuration from MASR bridge
            
        Returns:
            Configured supervisor instance or None if creation fails
        """
        
        try:
            # Get supervisor specification
            spec = self.supervisor_registry.get(config.supervisor_type)
            if not spec:
                logger.error(f"Unknown supervisor type: {config.supervisor_type}")
                self.factory_stats["failed_creations"] += 1
                return None
            
            # Check health status
            if not self.health_monitor.is_supervisor_healthy(config.supervisor_type):
                logger.warning(f"Supervisor {config.supervisor_type} is unhealthy, skipping creation")
                self.factory_stats["failed_creations"] += 1
                return None
            
            # Create supervisor configuration dict
            supervisor_config = {
                "max_workers": config.max_workers,
                "default_timeout": config.timeout_seconds,
                "quality_threshold": config.quality_threshold,
                "communication_protocol": {
                    "max_refinement_rounds": config.max_refinement_rounds,
                    "consensus_threshold": config.quality_threshold,
                },
                # Additional context from MASR
                "routing_context": config.context,
                "execution_mode": config.execution_mode,
            }
            
            # Instantiate supervisor
            kwargs: dict[str, Any] = {
                'gemini_service': None,
                'cache_client': None,
                'config': supervisor_config
            }
            supervisor_instance = spec.supervisor_class(**kwargs)  # type: BaseSupervisor

            # Update statistics
            self.factory_stats["total_created"] += 1
            self.factory_stats["successful_creations"] += 1

            logger.info(f"Created {config.supervisor_type} supervisor successfully")

            if isinstance(supervisor_instance, BaseSupervisor):
                return supervisor_instance
            return None
            
        except Exception as e:
            logger.error(f"Failed to create supervisor {config.supervisor_type}: {e}")
            self.factory_stats["failed_creations"] += 1
            return None
    
    async def select_optimal_supervisor(
        self, config: SupervisorConfiguration, task: AgentTask
    ) -> SupervisorSpecification | None:
        """
        Select optimal supervisor for configuration and task.
        
        Args:
            config: Supervisor configuration from MASR
            task: Task to be executed
            
        Returns:
            Best supervisor specification or None
        """
        
        # Extract requirements from config and task
        requirements = {
            "domain": config.domain,
            "complexity": self._infer_complexity(config, task),
            "strategy": config.routing_strategy,
            "workers": config.max_workers,
            "capabilities": self._extract_required_capabilities(config, task),
        }
        
        # Use capability matcher to find best supervisor
        selected = self.capability_matcher.match_supervisor(
            requirements, 
            self.get_available_supervisors(),
            self.health_monitor
        )
        
        return selected
    
    def _infer_complexity(self, config: SupervisorConfiguration, task: AgentTask) -> str:
        """Infer complexity level from configuration and task."""
        # Extract from MASR context if available
        context = config.context or {}
        complexity_analysis = context.get("complexity_analysis", {})
        
        complexity_level = complexity_analysis.get("level", "moderate")
        if isinstance(complexity_level, str):
            return complexity_level.lower()

        return "moderate"
    
    def _extract_required_capabilities(
        self, config: SupervisorConfiguration, task: AgentTask
    ) -> list[SupervisorCapability]:
        """Extract required capabilities from configuration and task."""
        capabilities = []
        
        # Domain-based capabilities
        domain_caps = {
            "research": [SupervisorCapability.RESEARCH, SupervisorCapability.LITERATURE_REVIEW],
            "content": [SupervisorCapability.CONTENT, SupervisorCapability.CONTENT_CREATION],
            "analytics": [SupervisorCapability.ANALYTICS, SupervisorCapability.DATA_ANALYSIS],
            "service": [SupervisorCapability.SERVICE],
        }
        
        capabilities.extend(domain_caps.get(config.domain, []))
        
        # Always require TalkHier and LangGraph for quality
        capabilities.extend([
            SupervisorCapability.TALKHIER_PROTOCOL,
            SupervisorCapability.LANGGRAPH_WORKFLOWS,
        ])
        
        # Add refinement capability if multiple rounds expected
        if config.max_refinement_rounds > 1:
            capabilities.append(SupervisorCapability.MULTI_ROUND_REFINEMENT)
        
        return capabilities
    
    def record_execution_result(
        self, supervisor_type: str, success: bool, execution_time_ms: int
    ) -> None:
        """Record execution result for health monitoring."""
        self.health_monitor.record_execution(supervisor_type, success, execution_time_ms)
    
    async def get_factory_stats(self) -> FactoryStatsDict:
        """Get factory statistics and health report."""
        return {
            "factory_stats": self.factory_stats.copy(),
            "registry": {
                "total_supervisors": len(self.supervisor_registry),
                "supervisor_types": list(self.supervisor_registry.keys()),
            },
            "health_report": await self.health_monitor.get_health_report(),
        }
    
    async def health_check(self) -> HealthCheckDict:
        """Perform factory health check."""
        health_report = await self.health_monitor.get_health_report()
        
        return {
            "status": "healthy" if health_report["healthy_supervisors"] > 0 else "unhealthy",
            "components": {
                "health_monitor": "healthy",
                "capability_matcher": "healthy",
                "supervisor_registry": "healthy",
            },
            "metrics": {
                "total_supervisors": len(self.supervisor_registry),
                "healthy_supervisors": health_report["healthy_supervisors"],
                "success_rate": (
                    self.factory_stats["successful_creations"] / 
                    max(self.factory_stats["total_created"], 1)
                ),
            }
        }


__all__ = [
    "CapabilityMatcher",
    "SupervisorCapability",
    "SupervisorFactory",
    "SupervisorHealthMonitor",
    "SupervisorSpecification",
]