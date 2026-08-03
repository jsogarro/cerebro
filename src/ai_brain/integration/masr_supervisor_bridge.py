"""
MASR-Supervisor Bridge

Core integration layer that translates MASR routing decisions into supervisor
execution plans and coordinates the hierarchical execution of multi-agent workflows.

This bridge enables intelligent end-to-end query processing by connecting:
1. MASR query analysis and routing decisions
2. Hierarchical supervisor/worker coordination
3. TalkHier protocol for quality assurance
4. Resource management and cost optimization
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from structlog import get_logger

from src.core.kernel import TypedRegistry
from src.core.kernel.component_keys import (
    AGENT_KEYS,
    COLLABORATION_MODE_WORKFLOW_KEY,
    DOMAIN_KEYS,
)
from src.core.types import HealthCheckDict

from ...agents.models import AgentResult, AgentTask
from ...agents.supervisors.base_supervisor import BaseSupervisor
from ...core.contracts import ExecutionPlan, WorkerAssignment
from ..router.masr import RoutingDecision, RoutingStrategy
from ..router.query_analyzer import ComplexityLevel
from .execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
    PlanExecutionResult,
)

logger = get_logger()


class SupervisorExecutionStatus(Enum):
    """Status of supervisor execution."""

    PENDING = "pending"
    INITIALIZING = "initializing"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SupervisorConfiguration:
    """Configuration for supervisor execution derived from MASR routing."""

    supervisor_type: str
    domain: str
    worker_allocation: list[str]
    quality_threshold: float
    max_refinement_rounds: int
    timeout_seconds: int

    # Execution mode from MASR collaboration mode
    execution_mode: str = "parallel"  # parallel, sequential, hybrid, adaptive

    # Resource constraints
    max_workers: int = 10
    max_parallel_workers: int = 5

    # Context from MASR routing
    routing_strategy: str = "balanced"
    estimated_cost: float = 0.0
    estimated_quality: float = 0.0
    confidence_score: float = 0.0

    # Additional context
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SupervisorExecutionResult:
    """Result of supervisor execution."""

    execution_id: str
    supervisor_type: str
    domain: str
    status: SupervisorExecutionStatus

    # Results
    agent_result: AgentResult | None = None
    supervision_quality: dict[str, float] = field(default_factory=dict)

    # Performance metrics
    execution_time_seconds: float = 0.0
    actual_cost: float = 0.0
    workers_used: int = 0
    refinement_rounds: int = 0

    # Quality assessment
    quality_score: float = 0.0
    consensus_score: float = 0.0
    confidence_score: float = 0.0

    # Error information
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Timestamps
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    # MASR feedback data
    routing_accuracy: float | None = None
    cost_accuracy: float | None = None

    def mark_completed(self) -> None:
        """Mark execution as completed."""
        self.completed_at = datetime.now()
        if self.started_at:
            self.execution_time_seconds = (
                self.completed_at - self.started_at
            ).total_seconds()


class RoutingDecisionTranslator:
    """Translates MASR routing decisions into supervisor configurations."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        component_registry: TypedRegistry | None = None,
    ):
        """Initialize translator with configuration."""
        self.config = config or {}
        if component_registry is None:
            from src.api.services.component_catalog import (
                get_default_component_registry,
            )

            component_registry = get_default_component_registry()
        self.component_registry = component_registry

        # Quality threshold mapping
        self.strategy_to_quality = {
            RoutingStrategy.SPEED_FIRST: 0.75,
            RoutingStrategy.COST_EFFICIENT: 0.80,
            RoutingStrategy.QUALITY_FOCUSED: 0.95,
            RoutingStrategy.BALANCED: 0.85,
            RoutingStrategy.ADAPTIVE: 0.85,
        }

    def translate(self, routing_decision: RoutingDecision) -> SupervisorConfiguration:
        """
        Translate MASR routing decision to supervisor configuration.

        Args:
            routing_decision: MASR routing decision to translate

        Returns:
            SupervisorConfiguration for execution
        """

        # Determine supervisor type from agent allocation
        supervisor_type = routing_decision.agent_allocation.supervisor_type

        # Map to our supervisor types if needed
        if supervisor_type in [
            "general",
            "parallel_coordinator",
            "hierarchical_supervisor",
        ]:
            # Use domain mapping for generic supervisor types
            primary_domain = self._get_primary_domain(routing_decision)
            domain_key = DOMAIN_KEYS.get(primary_domain, DOMAIN_KEYS["research"])
            supervisor_type = self.component_registry.resolve(domain_key)

        # Get quality threshold based on routing strategy
        routing_strategy = self._infer_routing_strategy(routing_decision)
        quality_threshold = self.strategy_to_quality.get(routing_strategy, 0.85)

        # Determine max refinement rounds based on complexity
        complexity_level = routing_decision.complexity_analysis.level
        max_refinement_rounds = self._calculate_max_refinement_rounds(
            complexity_level, routing_strategy
        )

        # Get execution mode from collaboration mode
        collaboration_resolver = self.component_registry.resolve(
            COLLABORATION_MODE_WORKFLOW_KEY
        )
        execution_mode = collaboration_resolver(routing_decision.collaboration_mode)

        return SupervisorConfiguration(
            supervisor_type=supervisor_type,
            domain=self._get_primary_domain(routing_decision),
            worker_allocation=routing_decision.agent_allocation.worker_types,
            quality_threshold=quality_threshold,
            max_refinement_rounds=max_refinement_rounds,
            timeout_seconds=routing_decision.agent_allocation.timeout_seconds,
            execution_mode=execution_mode,
            max_workers=routing_decision.agent_allocation.worker_count,
            max_parallel_workers=routing_decision.agent_allocation.max_parallel,
            routing_strategy=routing_strategy.value
            if hasattr(routing_strategy, "value")
            else str(routing_strategy),
            estimated_cost=routing_decision.estimated_cost,
            estimated_quality=routing_decision.estimated_quality,
            confidence_score=routing_decision.confidence_score,
            context={
                "query_id": routing_decision.query_id,
                "collaboration_mode": routing_decision.collaboration_mode.value,
                "complexity_analysis": {
                    "level": routing_decision.complexity_analysis.level.value,
                    "score": routing_decision.complexity_analysis.score,
                    "uncertainty": routing_decision.complexity_analysis.uncertainty,
                    "domains": [
                        d.value if hasattr(d, "value") else str(d)
                        for d in routing_decision.complexity_analysis.domains
                    ],
                },
                "context_requirements": routing_decision.context_requirements,
                "memory_allocation": routing_decision.memory_allocation,
            },
        )

    def _get_primary_domain(self, routing_decision: RoutingDecision) -> str:
        """Get primary domain from routing decision."""
        domains = routing_decision.complexity_analysis.domains
        if not domains:
            return "research"  # Default domain

        # Return first domain, or "research" if it's in the list
        domain_names = [d.value if hasattr(d, "value") else str(d) for d in domains]
        if "research" in domain_names:
            return "research"

        return domain_names[0] if domain_names else "research"

    def _infer_routing_strategy(
        self, routing_decision: RoutingDecision
    ) -> RoutingStrategy:
        """Infer routing strategy from routing decision context."""
        # This would normally be part of the routing decision
        # For now, use balanced as default
        return RoutingStrategy.BALANCED

    def _calculate_max_refinement_rounds(
        self, complexity_level: ComplexityLevel, routing_strategy: RoutingStrategy
    ) -> int:
        """Calculate max refinement rounds based on complexity and strategy."""

        base_rounds = {
            ComplexityLevel.SIMPLE: 1,
            ComplexityLevel.MODERATE: 2,
            ComplexityLevel.COMPLEX: 3,
        }

        rounds = base_rounds.get(complexity_level, 2)

        # Adjust for routing strategy
        if routing_strategy == RoutingStrategy.QUALITY_FOCUSED:
            rounds += 1
        elif routing_strategy == RoutingStrategy.SPEED_FIRST:
            rounds = max(1, rounds - 1)

        return min(rounds, 4)  # Cap at 4 rounds


class ResourcePool:
    """Resource pool for managing supervisor instances."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        gemini_service: Any | None = None,
        component_registry: TypedRegistry | None = None,
    ):
        """Initialize resource pool."""
        self.config = config or {}
        self.gemini_service = gemini_service
        if component_registry is None:
            from src.api.services.component_catalog import (
                get_default_component_registry,
            )

            component_registry = get_default_component_registry()
        self.component_registry = component_registry

        # Pool configuration
        self.max_pool_size = self.config.get("max_pool_size", 10)
        self.idle_timeout = self.config.get("idle_timeout_seconds", 300)  # 5 minutes

        # Supervisor pools by type
        self.supervisor_pools: dict[str, list[BaseSupervisor]] = {}
        self.active_supervisors: dict[str, BaseSupervisor] = {}

        # Pool statistics
        self.pool_stats = {
            "created": 0,
            "reused": 0,
            "evicted": 0,
            "active": 0,
        }

        # Cleanup task
        self._cleanup_task: asyncio.Task[None] | None = None

    async def get_supervisor(
        self, supervisor_class: type[BaseSupervisor], config: SupervisorConfiguration
    ) -> BaseSupervisor:
        """
        Get supervisor instance from pool or create new one.

        Args:
            supervisor_class: Supervisor class to instantiate
            config: Supervisor configuration

        Returns:
            Supervisor instance ready for execution
        """

        supervisor_type = config.supervisor_type

        # Try to get from pool first
        if supervisor_type in self.supervisor_pools:
            pool = self.supervisor_pools[supervisor_type]
            if pool:
                supervisor = pool.pop()
                self.pool_stats["reused"] += 1
                logger.debug(f"Reused supervisor {supervisor_type} from pool")
                return supervisor

        # Create new supervisor. ``BaseSupervisor.__init__`` requires
        # ``supervisor_type`` and ``domain`` positionally; pull them off the
        # config so subclasses inheriting the base signature collect.
        supervisor = supervisor_class(
            supervisor_type=config.supervisor_type,
            domain=config.domain,
            gemini_service=self.gemini_service,
            cache_client=None,
            config=self._create_supervisor_config(config),
        )

        self.pool_stats["created"] += 1
        logger.debug(f"Created new supervisor {supervisor_type}")

        return supervisor

    async def return_supervisor(self, supervisor: BaseSupervisor) -> None:
        """Return supervisor to pool for reuse."""

        supervisor_type = supervisor.supervisor_type

        # Initialize pool if needed
        if supervisor_type not in self.supervisor_pools:
            self.supervisor_pools[supervisor_type] = []

        pool = self.supervisor_pools[supervisor_type]

        # Add to pool if not at capacity
        if len(pool) < self.max_pool_size:
            pool.append(supervisor)
            logger.debug(f"Returned supervisor {supervisor_type} to pool")
        else:
            # Pool full, close supervisor
            await supervisor.close()
            self.pool_stats["evicted"] += 1
            logger.debug(f"Evicted supervisor {supervisor_type} - pool full")

    def _create_supervisor_config(
        self, config: SupervisorConfiguration
    ) -> dict[str, Any]:
        """Create supervisor-specific configuration."""
        return {
            "max_workers": config.max_workers,
            "default_timeout": config.timeout_seconds,
            "quality_threshold": config.quality_threshold,
            "communication_protocol": {
                "max_refinement_rounds": config.max_refinement_rounds,
                "consensus_threshold": config.quality_threshold,
            },
            "component_registry": self.component_registry,
        }

    async def cleanup(self) -> None:
        """Cleanup idle supervisors from pools."""
        total_evicted = 0

        for _supervisor_type, pool in self.supervisor_pools.items():
            # For simplicity, clear idle supervisors
            # In production, this would check last_used timestamps
            while pool:
                supervisor = pool.pop()
                await supervisor.close()
                total_evicted += 1

        self.pool_stats["evicted"] += total_evicted
        logger.debug(f"Cleaned up {total_evicted} idle supervisors")

    async def get_stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        return {
            "pool_stats": self.pool_stats.copy(),
            "active_pools": {
                supervisor_type: len(pool)
                for supervisor_type, pool in self.supervisor_pools.items()
            },
            "total_pooled": sum(len(pool) for pool in self.supervisor_pools.values()),
            "active_supervisors": len(self.active_supervisors),
        }


class SupervisorExecutor:
    """Manages supervisor execution lifecycle."""

    def __init__(
        self, resource_pool: ResourcePool, config: dict[str, Any] | None = None
    ):
        """Initialize supervisor executor."""
        self.resource_pool = resource_pool
        self.config = config or {}

        # Execution tracking
        self.active_executions: dict[str, SupervisorExecutionResult] = {}

        # Performance metrics
        self.execution_stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "average_execution_time": 0.0,
            "average_quality_score": 0.0,
        }

    async def execute(
        self,
        supervisor_class: type[BaseSupervisor],
        config: SupervisorConfiguration,
        task: AgentTask,
    ) -> SupervisorExecutionResult:
        """
        Execute task using specified supervisor.

        Args:
            supervisor_class: Supervisor class to use
            config: Supervisor configuration
            task: Task to execute

        Returns:
            Execution result with performance metrics
        """

        execution_id = str(uuid.uuid4())
        start_time = datetime.now()

        # Create execution result tracker
        result = SupervisorExecutionResult(
            execution_id=execution_id,
            supervisor_type=config.supervisor_type,
            domain=config.domain,
            status=SupervisorExecutionStatus.INITIALIZING,
            started_at=start_time,
        )

        self.active_executions[execution_id] = result

        try:
            # Get supervisor from pool
            result.status = SupervisorExecutionStatus.INITIALIZING
            supervisor = await self.resource_pool.get_supervisor(
                supervisor_class, config
            )

            # Execute task
            result.status = SupervisorExecutionStatus.EXECUTING
            logger.info(
                f"Executing task {task.id} with {config.supervisor_type} supervisor"
            )

            agent_result = await supervisor.execute(task)

            # Process result
            result.agent_result = agent_result
            result.status = SupervisorExecutionStatus.COMPLETED
            result.quality_score = agent_result.confidence

            # Extract supervision quality metrics
            if hasattr(agent_result, "output") and isinstance(
                agent_result.output, dict
            ):
                supervision_quality = agent_result.output.get("supervision_quality", {})
                result.supervision_quality = supervision_quality
                result.consensus_score = supervision_quality.get(
                    "final_consensus_score", 0.0
                )
                result.refinement_rounds = supervision_quality.get(
                    "refinement_rounds", 0
                )

                coordination_metadata = agent_result.output.get(
                    "coordination_metadata", {}
                )
                result.workers_used = len(coordination_metadata.get("workers_used", []))

            # Return supervisor to pool
            await self.resource_pool.return_supervisor(supervisor)

            # Update statistics
            self.execution_stats["successful_executions"] += 1

            logger.info(f"Completed execution {execution_id} successfully")

        except Exception as e:
            logger.error(f"Execution {execution_id} failed: {e}")
            result.status = SupervisorExecutionStatus.FAILED
            result.errors.append(str(e))
            self.execution_stats["failed_executions"] += 1

        finally:
            # Mark as completed and update stats
            result.mark_completed()
            self.execution_stats["total_executions"] += 1

            # Update average execution time
            total_time = (
                self.execution_stats["average_execution_time"]
                * (self.execution_stats["total_executions"] - 1)
                + result.execution_time_seconds
            )
            self.execution_stats["average_execution_time"] = (
                total_time / self.execution_stats["total_executions"]
            )

            # Remove from active executions
            if execution_id in self.active_executions:
                del self.active_executions[execution_id]

        return result

    async def get_execution_status(
        self, execution_id: str
    ) -> SupervisorExecutionResult | None:
        """Get status of active execution."""
        return self.active_executions.get(execution_id)

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel active execution."""
        if execution_id in self.active_executions:
            result = self.active_executions[execution_id]
            result.status = SupervisorExecutionStatus.CANCELLED
            return True
        return False

    async def get_stats(self) -> dict[str, Any]:
        """Get execution statistics."""
        return {
            "execution_stats": self.execution_stats.copy(),
            "active_executions": len(self.active_executions),
            "resource_pool": await self.resource_pool.get_stats(),
        }


class MASRSupervisorBridge:
    """
    Main bridge class integrating MASR routing with supervisor execution.

    This is the primary interface for routing queries through the MASR system
    and executing them via hierarchical supervisors with TalkHier protocol.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        gemini_service: Any | None = None,
        component_registry: TypedRegistry | None = None,
    ):
        """Initialize MASR-Supervisor bridge."""
        self.config = config or {}
        self.gemini_service = gemini_service
        if component_registry is None:
            from src.api.services.component_catalog import (
                get_default_component_registry,
            )

            component_registry = get_default_component_registry()
        self.component_registry = component_registry

        # Initialize components
        self.translator = RoutingDecisionTranslator(
            self.config.get("translator", {}),
            component_registry=self.component_registry,
        )
        self.resource_pool = ResourcePool(
            self.config.get("resource_pool", {}),
            gemini_service=gemini_service,
            component_registry=self.component_registry,
        )
        self.executor = SupervisorExecutor(
            self.resource_pool,
            self.config.get("executor", {}),
        )
        self.plan_topology_executor = ExecutionPlanTopologyExecutor(
            worker_executor=self._execute_plan_worker,
            capability_checker=self._has_plan_worker_capability,
        )

        # Bridge statistics
        self.bridge_stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "average_response_time": 0.0,
            "routing_accuracy": 0.0,
        }

    def _has_plan_worker_capability(self, worker_type: str) -> bool:
        key = AGENT_KEYS.get(worker_type)
        return key is not None and key in self.component_registry.keys

    async def _execute_plan_worker(
        self,
        worker: WorkerAssignment,
        task: AgentTask,
    ) -> AgentResult:
        worker_class = self.component_registry.resolve(
            AGENT_KEYS[str(worker.worker_type)]
        )
        worker_instance = worker_class(
            gemini_service=self.gemini_service,
            cache_client=None,
            config={
                "permission_scopes": list(worker.permission_scopes),
                "tool_allowlist": list(worker.tool_allowlist),
            },
        )
        return await worker_instance.execute(task)

    def admit_execution_plan(self, execution_plan: ExecutionPlan) -> None:
        """Synchronously validate plan-backed execution before service state exists."""

        self.plan_topology_executor.admit(execution_plan)

    async def execute_execution_plan(
        self,
        execution_plan: ExecutionPlan,
        task: AgentTask,
    ) -> PlanExecutionResult:
        """Execute a compiled plan without translating it to a supervisor graph."""

        return await self.plan_topology_executor.execute(execution_plan, task)

    async def get_bridge_stats(self) -> dict[str, Any]:
        """Get comprehensive bridge statistics."""
        return {
            "bridge": self.bridge_stats.copy(),
            "translator": {"active": True},  # Could add translator stats
            "executor": await self.executor.get_stats(),
        }

    async def health_check(self) -> HealthCheckDict:
        """Perform health check on bridge components."""
        await self.get_bridge_stats()
        health_dict: HealthCheckDict = {
            "status": "healthy",
            "metrics": {
                "total_requests": self.bridge_stats["total_requests"],
                "active_executions": len(self.executor.active_executions),
            },
            "components": {
                "bridge": "active",
                "executor": "active",
            },
        }
        return health_dict

    async def cleanup(self) -> None:
        """Cleanup bridge resources."""
        await self.resource_pool.cleanup()
        logger.info("MASR-Supervisor bridge cleanup completed")


__all__ = [
    "MASRSupervisorBridge",
    "ResourcePool",
    "RoutingDecisionTranslator",
    "SupervisorConfiguration",
    "SupervisorExecutionResult",
    "SupervisorExecutionStatus",
    "SupervisorExecutor",
]
