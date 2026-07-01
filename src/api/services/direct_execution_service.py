"""
Direct Execution Service

Replaces Temporal workflows with direct MASR routing and supervisor execution.
Provides the same functionality as Temporal workflows but with a simpler,
more responsive architecture optimized for interactive AI queries.

Key Features:
- Direct MASR routing without workflow serialization overhead
- Real-time progress tracking via WebSocket events
- Simplified error handling and retry logic
- Integration with hierarchical supervisor/worker coordination
- State management via LangGraph workflows
"""

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from structlog import get_logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.constants import DEFAULT_AGENT_TIMEOUT, MAX_RETRY_ATTEMPTS
from src.repositories.checkpoint_repository import CheckpointRepository

from ...agents.models import AgentTask
from ...agents.supervisors.analytics_supervisor import AnalyticsSupervisor
from ...agents.supervisors.content_supervisor import ContentSupervisor
from ...agents.supervisors.finance_supervisor import FinanceSupervisor
from ...agents.supervisors.research_supervisor import ResearchSupervisor
from ...agents.supervisors.supervisor_factory import SupervisorFactory
from ...ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from ...ai_brain.router.masr import MASRouter
from ...models.research_project import ResearchProject
from ...models.websocket_messages import ProgressUpdate
from .event_publisher import EventPublisher

logger = get_logger()


@dataclass
class ExecutionStatus:
    """Status of direct execution."""

    execution_id: str
    project_id: str
    status: str  # pending, running, completed, failed
    progress_percentage: float = 0.0
    current_phase: str = "initialization"

    # Results
    agent_results: dict[str, Any] = field(default_factory=dict)
    quality_scores: dict[str, float] = field(default_factory=dict)
    final_output: dict[str, Any] | None = None

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    execution_time_seconds: float = 0.0

    # MASR routing information
    routing_decision: dict[str, Any] | None = None
    supervisor_type: str | None = None
    workers_used: int = 0

    # Error handling
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retry_count: int = 0


class DirectExecutionService:
    """
    Direct execution service using MASR routing and supervisor coordination.

    Replaces Temporal workflows with simplified direct execution that provides
    the same functionality with better performance and easier debugging.
    """

    def __init__(
        self,
        masr_router: MASRouter | None = None,
        supervisor_bridge: MASRSupervisorBridge | None = None,
        supervisor_factory: SupervisorFactory | None = None,
        event_publisher: EventPublisher | None = None,
        gemini_service: Any | None = None,
        session_factory: Any | None = None,
    ):
        """Initialize direct execution service."""

        # Initialize components (would be injected in production)
        self.gemini_service = gemini_service
        self.masr_router = masr_router or MASRouter()
        self.supervisor_bridge = supervisor_bridge or MASRSupervisorBridge(
            gemini_service=gemini_service,
        )
        self.supervisor_factory = supervisor_factory or SupervisorFactory()
        self.event_publisher = event_publisher
        self.session_factory = session_factory

        # Execution tracking
        self.active_executions: dict[str, ExecutionStatus] = {}

        # Service configuration
        self.max_concurrent_executions = 50
        self.default_timeout_seconds = DEFAULT_AGENT_TIMEOUT
        self.enable_retry = True
        self.max_retries = MAX_RETRY_ATTEMPTS

        # Store background task references to prevent GC
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Performance metrics
        self.execution_stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "average_execution_time": 0.0,
            "concurrent_executions": 0,
        }

    async def _checkpoint(self, execution_status: ExecutionStatus, phase: str) -> None:
        """
        Create a checkpoint for the current execution state.

        Gracefully degrades when DB is unavailable — logs and continues.
        """
        if not self.session_factory:
            return

        try:
            async with self.session_factory() as session:
                repository = CheckpointRepository(session)

                checkpoint_data = {
                    "status": execution_status.status,
                    "progress_percentage": execution_status.progress_percentage,
                    "current_phase": execution_status.current_phase,
                    "routing_decision": execution_status.routing_decision,
                    "supervisor_type": execution_status.supervisor_type,
                    "agent_results": execution_status.agent_results,
                    "quality_scores": execution_status.quality_scores,
                    "final_output": execution_status.final_output,
                    "workers_used": execution_status.workers_used,
                    "errors": execution_status.errors,
                    "warnings": execution_status.warnings,
                    "retry_count": execution_status.retry_count,
                }

                execution_metrics = {
                    "started_at": execution_status.started_at.isoformat(),
                    "execution_time_seconds": execution_status.execution_time_seconds,
                }

                await repository.create_checkpoint(
                    workflow_id=execution_status.execution_id,
                    project_id=uuid.UUID(execution_status.project_id),
                    checkpoint_data=checkpoint_data,
                    phase=phase,
                    checkpoint_type="automatic",
                    execution_metrics=execution_metrics,
                )
                await session.commit()

                logger.debug(
                    "Created checkpoint",
                    execution_id=execution_status.execution_id,
                    phase=phase,
                )

        except Exception as e:
            logger.warning(
                "Failed to create checkpoint (degrading gracefully)",
                execution_id=execution_status.execution_id,
                phase=phase,
                error=str(e),
            )

    async def start_research_execution(
        self, project: ResearchProject, context: dict[str, Any] | None = None
    ) -> str:
        """
        Start direct research execution using MASR routing.

        Args:
            project: Research project to execute
            context: Additional execution context

        Returns:
            Execution ID for tracking progress
        """

        execution_id = str(uuid.uuid4())

        # Check capacity
        if len(self.active_executions) >= self.max_concurrent_executions:
            raise RuntimeError(
                f"Maximum concurrent executions ({self.max_concurrent_executions}) reached"
            )

        # Create execution status
        execution_status = ExecutionStatus(
            execution_id=execution_id,
            project_id=str(project.id),
            status="pending",
            current_phase="initialization",
        )

        self.active_executions[execution_id] = execution_status
        self.execution_stats["total_executions"] += 1
        self.execution_stats["concurrent_executions"] += 1

        # Start execution asynchronously
        task = asyncio.create_task(
            self._execute_research_workflow(project, execution_status, context)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        logger.info(f"Started direct execution {execution_id} for project {project.id}")

        return execution_id

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def _execute_research_workflow(
        self,
        project: ResearchProject,
        execution_status: ExecutionStatus,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Execute research workflow with retry logic.

        Args:
            project: Research project to execute
            execution_status: Execution status tracker
            context: Additional context
        """

        try:
            execution_status.status = "running"
            execution_status.current_phase = "masr_routing"

            await self._publish_progress_update(execution_status)

            # Step 1: MASR Routing
            logger.info(f"Getting MASR routing for project {project.id}")

            routing_context = {
                "query": project.query.text,
                "domains": project.query.domains,
                "project_id": project.id,
                "user_id": str(project.user_id) if project.user_id else None,
            }

            if context:
                routing_context.update(context)

            routing_decision = await self.masr_router.route(
                query=project.query.text, context=routing_context
            )

            execution_status.routing_decision = asdict(routing_decision)
            execution_status.supervisor_type = (
                routing_decision.agent_allocation.supervisor_type
            )
            execution_status.current_phase = "supervisor_execution"
            execution_status.progress_percentage = 20.0

            await self._publish_progress_update(execution_status)
            await self._checkpoint(execution_status, "masr_routing")

            # Step 2: Supervisor Execution
            logger.info(
                f"Executing via {routing_decision.agent_allocation.supervisor_type} supervisor"
            )

            agent_task = AgentTask(
                id=f"research_{project.id}_{execution_status.execution_id}",
                agent_type="research",
                input_data={
                    "query": project.query.text,
                    "domains": project.query.domains,
                    "context": routing_context,
                    "project_data": {
                        "title": project.title,
                        "scope": project.scope.model_dump() if project.scope else {},
                        "query": project.query.model_dump(),
                    },
                    "routing_decision": asdict(routing_decision),
                },
            )

            # Get supervisor registry
            from ...agents.supervisors.base_supervisor import BaseSupervisor

            supervisor_registry: dict[str, type[BaseSupervisor]] = {
                "research": ResearchSupervisor,
                "content": ContentSupervisor,
                "analytics": AnalyticsSupervisor,
                "finance": FinanceSupervisor,
            }

            execution_status.current_phase = "hierarchical_coordination"
            execution_status.progress_percentage = 40.0
            await self._publish_progress_update(execution_status)
            await self._checkpoint(execution_status, "supervisor_execution")

            # Execute via MASR-Supervisor bridge
            supervisor_result = await self.supervisor_bridge.execute_routing_decision(
                routing_decision=routing_decision,
                task=agent_task,
                supervisor_registry=supervisor_registry,
            )

            execution_status.progress_percentage = 80.0
            execution_status.current_phase = "result_processing"
            await self._publish_progress_update(execution_status)

            # Process results
            if (
                supervisor_result.status.value == "completed"
                and supervisor_result.agent_result
            ):
                execution_status.agent_results = supervisor_result.agent_result.output
                execution_status.quality_scores = {
                    "overall": supervisor_result.quality_score,
                    "consensus": supervisor_result.consensus_score,
                }
                execution_status.workers_used = supervisor_result.workers_used

                # Extract final output
                if isinstance(supervisor_result.agent_result.output, dict):
                    execution_status.final_output = (
                        supervisor_result.agent_result.output
                    )

                execution_status.status = "completed"
                execution_status.progress_percentage = 100.0
                execution_status.current_phase = "completed"

                self.execution_stats["successful_executions"] += 1

                await self._checkpoint(execution_status, "completed")

                logger.info(
                    f"Direct execution {execution_status.execution_id} completed successfully"
                )

            else:
                # Execution failed or incomplete
                execution_status.status = "failed"
                execution_status.errors.extend(supervisor_result.errors)
                execution_status.current_phase = "failed"

                self.execution_stats["failed_executions"] += 1

                logger.error(
                    f"Direct execution {execution_status.execution_id} failed: {supervisor_result.errors}"
                )

        except Exception as e:
            logger.error(
                f"Direct execution {execution_status.execution_id} failed with exception: {e}"
            )

            execution_status.status = "failed"
            execution_status.errors.append(str(e))
            execution_status.current_phase = "failed"
            execution_status.retry_count += 1

            self.execution_stats["failed_executions"] += 1

            # Re-raise for retry logic
            raise

        finally:
            # Update completion time and metrics. Use timezone-aware UTC to
            # match ``started_at`` (``datetime.now(UTC)``); a naive
            # ``datetime.now()`` here raised "can't subtract offset-naive and
            # offset-aware datetimes" from this finally block on every run,
            # which — because it propagates even after the body succeeds —
            # tripped the @retry wrapper and re-ran the whole workflow.
            execution_status.completed_at = datetime.now(UTC)
            execution_status.execution_time_seconds = (
                execution_status.completed_at - execution_status.started_at
            ).total_seconds()

            # Update average execution time
            if self.execution_stats["total_executions"] > 0:
                current_avg = self.execution_stats["average_execution_time"]
                total_executions = self.execution_stats["total_executions"]
                new_avg = (
                    current_avg * (total_executions - 1)
                    + execution_status.execution_time_seconds
                ) / total_executions
                self.execution_stats["average_execution_time"] = new_avg

            self.execution_stats["concurrent_executions"] -= 1

            # Final progress update
            await self._publish_progress_update(execution_status)

    async def get_execution_status(self, execution_id: str) -> ExecutionStatus | None:
        """Get current status of execution."""
        return self.active_executions.get(execution_id)

    async def get_execution_results(self, execution_id: str) -> dict[str, Any] | None:
        """Get results of completed execution."""

        execution = self.active_executions.get(execution_id)
        if not execution:
            return None

        if execution.status == "completed":
            return execution.final_output
        elif execution.status == "failed":
            return {"error": "Execution failed", "details": execution.errors}
        else:
            return {
                "status": execution.status,
                "progress": execution.progress_percentage,
            }

    async def resume_execution(self, project_id: uuid.UUID) -> str | None:
        """
        Resume execution from the latest recoverable checkpoint.

        Args:
            project_id: Project UUID to resume

        Returns:
            Execution ID if resumed successfully, None otherwise
        """
        if not self.session_factory:
            logger.warning(
                "Cannot resume execution without database",
                project_id=str(project_id),
            )
            return None

        try:
            async with self.session_factory() as session:
                repository = CheckpointRepository(session)
                checkpoint = await repository.get_recovery_point(project_id)

                if not checkpoint:
                    logger.info(
                        "No recoverable checkpoint found",
                        project_id=str(project_id),
                    )
                    return None

                # Restore checkpoint data
                restored_data = await repository.restore_from_checkpoint(checkpoint.id)

                if not restored_data:
                    logger.warning(
                        "Failed to restore checkpoint data",
                        checkpoint_id=str(checkpoint.id),
                    )
                    return None

                # Rebuild ExecutionStatus from checkpoint
                checkpoint_data = restored_data["checkpoint_data"]
                execution_id: str = str(restored_data["workflow_id"])

                execution_status = ExecutionStatus(
                    execution_id=execution_id,
                    project_id=str(project_id),
                    status=checkpoint_data["status"],
                    progress_percentage=checkpoint_data["progress_percentage"],
                    current_phase=checkpoint_data["current_phase"],
                    routing_decision=checkpoint_data.get("routing_decision"),
                    supervisor_type=checkpoint_data.get("supervisor_type"),
                    agent_results=checkpoint_data.get("agent_results", {}),
                    quality_scores=checkpoint_data.get("quality_scores", {}),
                    final_output=checkpoint_data.get("final_output"),
                    workers_used=checkpoint_data.get("workers_used", 0),
                    errors=checkpoint_data.get("errors", []),
                    warnings=checkpoint_data.get("warnings", []),
                    retry_count=checkpoint_data.get("retry_count", 0),
                )

                # Register in active executions
                self.active_executions[execution_id] = execution_status

                logger.info(
                    "Resumed execution from checkpoint",
                    execution_id=execution_id,
                    project_id=str(project_id),
                    phase=restored_data["phase"],
                )

                return execution_id

        except Exception as e:
            logger.error(
                "Failed to resume execution",
                project_id=str(project_id),
                error=str(e),
            )
            return None

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel active execution."""

        execution = self.active_executions.get(execution_id)
        if not execution:
            return False

        if execution.status in ["pending", "running"]:
            execution.status = "cancelled"
            execution.current_phase = "cancelled"

            await self._publish_progress_update(execution)

            logger.info(f"Cancelled execution {execution_id}")
            return True

        return False

    async def list_active_executions(self) -> list[ExecutionStatus]:
        """List all active executions."""
        return [
            execution
            for execution in self.active_executions.values()
            if execution.status in ["pending", "running"]
        ]

    async def cleanup_completed_executions(self, max_age_hours: int = 24) -> int:
        """Clean up old completed executions."""

        cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)

        executions_to_remove = [
            execution_id
            for execution_id, execution in self.active_executions.items()
            if (
                execution.status in ["completed", "failed", "cancelled"]
                and execution.completed_at
                and execution.completed_at < cutoff_time
            )
        ]

        for execution_id in executions_to_remove:
            del self.active_executions[execution_id]

        logger.info(f"Cleaned up {len(executions_to_remove)} old executions")

        return len(executions_to_remove)

    async def _publish_progress_update(self, execution_status: ExecutionStatus) -> None:
        """Broadcast a progress update to subscribed WebSocket clients.

        Uses the typed ``EventPublisher.publish_progress_update`` path, which
        actually fans out over WebSocket via
        ``connection_manager.broadcast_to_project``. The previous
        ``publish_project_event`` call was a logs-only compatibility shim, so
        no PROGRESS event ever reached a subscribed ``/ws`` client during a
        live query.
        """

        if not self.event_publisher:
            return

        # ``broadcast_to_project`` keys subscriptions by ``UUID``; the execution
        # project_id is stored as a string, so coerce it and skip the update if
        # it is not a valid UUID rather than crashing the execution loop.
        try:
            project_uuid = uuid.UUID(str(execution_status.project_id))
        except (ValueError, TypeError):
            logger.warning(
                "Skipping progress broadcast for non-UUID project_id",
                project_id=execution_status.project_id,
                execution_id=execution_status.execution_id,
            )
            return

        try:
            progress = ProgressUpdate(
                progress_percentage=execution_status.progress_percentage,
                current_phase=execution_status.current_phase,
                current_agent=execution_status.supervisor_type,
            )

            await self.event_publisher.publish_progress_update(project_uuid, progress)

        except Exception as e:
            logger.warning(f"Failed to publish progress update: {e}")

    async def get_service_stats(self) -> dict[str, Any]:
        """Get service statistics."""

        return {
            "execution_stats": self.execution_stats.copy(),
            "active_executions": len(self.active_executions),
            "active_execution_details": [
                {
                    "execution_id": execution.execution_id,
                    "project_id": execution.project_id,
                    "status": execution.status,
                    "progress": execution.progress_percentage,
                    "current_phase": execution.current_phase,
                    "duration": (
                        datetime.now(UTC) - execution.started_at
                    ).total_seconds(),
                }
                for execution in self.active_executions.values()
                if execution.status in ["pending", "running"]
            ],
            "component_health": {
                "masr_router": "healthy" if self.masr_router else "unavailable",
                "supervisor_bridge": "healthy"
                if self.supervisor_bridge
                else "unavailable",
                "supervisor_factory": "healthy"
                if self.supervisor_factory
                else "unavailable",
            },
        }

    async def health_check(self) -> dict[str, Any]:
        """Perform health check on service components."""

        components: dict[str, str] = {}

        # Check MASR router health
        if self.masr_router:
            try:
                masr_health = await self.masr_router.health_check()
                components["masr_router"] = str(masr_health.get("status", "unknown"))
            except Exception as e:
                components["masr_router"] = f"unhealthy: {e}"
        else:
            components["masr_router"] = "unavailable"

        # Check supervisor bridge health
        if self.supervisor_bridge:
            try:
                bridge_health = await self.supervisor_bridge.health_check()
                components["supervisor_bridge"] = str(
                    bridge_health.get("status", "unknown")
                )
            except Exception as e:
                components["supervisor_bridge"] = f"unhealthy: {e}"
        else:
            components["supervisor_bridge"] = "unavailable"

        # Check supervisor factory health
        if self.supervisor_factory:
            try:
                factory_health = await self.supervisor_factory.health_check()
                components["supervisor_factory"] = str(
                    factory_health.get("status", "unknown")
                )
            except Exception as e:
                components["supervisor_factory"] = f"unhealthy: {e}"
        else:
            components["supervisor_factory"] = "unavailable"

        # Determine overall health
        component_statuses = list(components.values())
        if all("healthy" in status for status in component_statuses):
            overall_status = "healthy"
        elif any("unhealthy" in status for status in component_statuses):
            overall_status = "degraded"
        else:
            overall_status = "unknown"

        health = {
            "status": overall_status,
            "components": components,
            "active_executions": len(self.active_executions),
            "service_stats": self.execution_stats,
        }

        return health


# Legacy compatibility functions for migration
async def create_research_plan(project_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning(
        "Using legacy create_research_plan - should migrate to direct execution"
    )

    return {
        "plan_created": True,
        "project_id": project_data.get("id"),
        "note": "Migrated from Temporal to direct execution",
        "timestamp": datetime.now().isoformat(),
    }


async def execute_agent_task(agent_task_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy execute_agent_task - should use supervisor execution")

    return {
        "task_completed": True,
        "agent_type": agent_task_data.get("agent_type", "unknown"),
        "note": "Migrated from Temporal activity to supervisor execution",
        "timestamp": datetime.now().isoformat(),
    }


async def aggregate_results(results_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy aggregate_results - handled by supervisors now")

    return {
        "aggregation_completed": True,
        "results_count": len(results_data.get("results", [])),
        "note": "Migrated from Temporal activity to supervisor result aggregation",
        "timestamp": datetime.now().isoformat(),
    }


# Global service instance (would be properly injected in production)
_direct_execution_service: DirectExecutionService | None = None


def get_direct_execution_service(
    gemini_service: Any | None = None,
) -> DirectExecutionService:
    """Get global direct execution service instance."""
    global _direct_execution_service

    if _direct_execution_service is None:
        _direct_execution_service = DirectExecutionService(
            gemini_service=gemini_service
        )

    return _direct_execution_service


__all__ = [
    "DirectExecutionService",
    "ExecutionStatus",
    "aggregate_results",
    # Legacy compatibility exports
    "create_research_plan",
    "execute_agent_task",
    "get_direct_execution_service",
]
