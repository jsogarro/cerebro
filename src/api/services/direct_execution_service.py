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
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.constants import DEFAULT_AGENT_TIMEOUT, MAX_RETRY_ATTEMPTS
from ...ai_brain.router.masr import MASRouter
from ...ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from ...agents.supervisors.supervisor_factory import SupervisorFactory
from ...agents.supervisors.research_supervisor import ResearchSupervisor
from ...agents.models import AgentTask, AgentResult
from ...models.research_project import ResearchProject
from ..websocket.event_publisher import EventPublisher

logger = logging.getLogger(__name__)


@dataclass
class ExecutionStatus:
    """Status of direct execution."""
    
    execution_id: str
    project_id: str
    status: str  # pending, running, completed, failed
    progress_percentage: float = 0.0
    current_phase: str = "initialization"
    
    # Results
    agent_results: Dict[str, Any] = None
    quality_scores: Dict[str, float] = None
    final_output: Optional[Dict[str, Any]] = None
    
    # Timing
    started_at: datetime = None
    completed_at: Optional[datetime] = None
    execution_time_seconds: float = 0.0
    
    # MASR routing information
    routing_decision: Optional[Dict[str, Any]] = None
    supervisor_type: Optional[str] = None
    workers_used: int = 0
    
    # Error handling
    errors: List[str] = None
    warnings: List[str] = None
    retry_count: int = 0
    
    def __post_init__(self):
        if self.agent_results is None:
            self.agent_results = {}
        if self.quality_scores is None:
            self.quality_scores = {}
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.started_at is None:
            self.started_at = datetime.now()


class DirectExecutionService:
    """
    Direct execution service using MASR routing and supervisor coordination.
    
    Replaces Temporal workflows with simplified direct execution that provides
    the same functionality with better performance and easier debugging.
    """
    
    def __init__(
        self,
        masr_router: Optional[MASRouter] = None,
        supervisor_bridge: Optional[MASRSupervisorBridge] = None,
        supervisor_factory: Optional[SupervisorFactory] = None,
        event_publisher: Optional[EventPublisher] = None,
    ):
        """Initialize direct execution service."""
        
        # Initialize components (would be injected in production)
        self.masr_router = masr_router or MASRouter()
        self.supervisor_bridge = supervisor_bridge or MASRSupervisorBridge()
        self.supervisor_factory = supervisor_factory or SupervisorFactory()
        self.event_publisher = event_publisher
        
        # Execution tracking
        self.active_executions: Dict[str, ExecutionStatus] = {}
        
        # Service configuration
        self.max_concurrent_executions = 50
        self.default_timeout_seconds = DEFAULT_AGENT_TIMEOUT
        self.enable_retry = True
        self.max_retries = MAX_RETRY_ATTEMPTS
        
        # Performance metrics
        self.execution_stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "average_execution_time": 0.0,
            "concurrent_executions": 0,
        }
    
    async def start_research_execution(
        self, 
        project: ResearchProject,
        context: Optional[Dict[str, Any]] = None
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
            raise RuntimeError(f"Maximum concurrent executions ({self.max_concurrent_executions}) reached")
        
        # Create execution status
        execution_status = ExecutionStatus(
            execution_id=execution_id,
            project_id=project.id,
            status="pending",
            current_phase="initialization"
        )
        
        self.active_executions[execution_id] = execution_status
        self.execution_stats["total_executions"] += 1
        self.execution_stats["concurrent_executions"] += 1
        
        # Start execution asynchronously
        asyncio.create_task(self._execute_research_workflow(project, execution_status, context))
        
        logger.info(f"Started direct execution {execution_id} for project {project.id}")
        
        return execution_id
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def _execute_research_workflow(
        self,
        project: ResearchProject, 
        execution_status: ExecutionStatus,
        context: Optional[Dict[str, Any]] = None
    ):
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
                "query": project.query,
                "domains": project.domains,
                "project_id": project.id,
                "user_id": str(project.user_id) if project.user_id else None,
            }
            
            if context:
                routing_context.update(context)
            
            routing_decision = await self.masr_router.route(
                query=project.query,
                context=routing_context
            )
            
            execution_status.routing_decision = asdict(routing_decision)
            execution_status.supervisor_type = routing_decision.agent_allocation.supervisor_type
            execution_status.current_phase = "supervisor_execution"
            execution_status.progress_percentage = 20.0
            
            await self._publish_progress_update(execution_status)
            
            # Step 2: Supervisor Execution
            logger.info(f"Executing via {routing_decision.agent_allocation.supervisor_type} supervisor")
            
            agent_task = AgentTask(
                id=f"research_{project.id}_{execution_status.execution_id}",
                agent_type="research",
                input_data={
                    "query": project.query,
                    "domains": project.domains,
                    "context": routing_context,
                    "project_data": {
                        "title": project.title,
                        "description": project.description,
                        "scope": project.scope,
                        "query": project.query.__dict__ if hasattr(project.query, '__dict__') else {},
                    },
                    "routing_decision": routing_decision.__dict__,
                }
            )
            
            # Get supervisor registry
            supervisor_registry = {
                "research": ResearchSupervisor
            }
            
            execution_status.current_phase = "hierarchical_coordination"
            execution_status.progress_percentage = 40.0
            await self._publish_progress_update(execution_status)
            
            # Execute via MASR-Supervisor bridge
            supervisor_result = await self.supervisor_bridge.execute_routing_decision(
                routing_decision=routing_decision,
                task=agent_task,
                supervisor_registry=supervisor_registry
            )
            
            execution_status.progress_percentage = 80.0
            execution_status.current_phase = "result_processing"
            await self._publish_progress_update(execution_status)
            
            # Process results
            if supervisor_result.status.value == "completed" and supervisor_result.agent_result:
                execution_status.agent_results = supervisor_result.agent_result.output
                execution_status.quality_scores = {
                    "overall": supervisor_result.quality_score,
                    "consensus": supervisor_result.consensus_score,
                }
                execution_status.workers_used = supervisor_result.workers_used
                
                # Extract final output
                if isinstance(supervisor_result.agent_result.output, dict):
                    execution_status.final_output = supervisor_result.agent_result.output
                
                execution_status.status = "completed"
                execution_status.progress_percentage = 100.0
                execution_status.current_phase = "completed"
                
                self.execution_stats["successful_executions"] += 1
                
                logger.info(f"Direct execution {execution_status.execution_id} completed successfully")
                
            else:
                # Execution failed or incomplete
                execution_status.status = "failed"
                execution_status.errors.extend(supervisor_result.errors)
                execution_status.current_phase = "failed"
                
                self.execution_stats["failed_executions"] += 1
                
                logger.error(f"Direct execution {execution_status.execution_id} failed: {supervisor_result.errors}")
            
        except Exception as e:
            logger.error(f"Direct execution {execution_status.execution_id} failed with exception: {e}")
            
            execution_status.status = "failed"
            execution_status.errors.append(str(e))
            execution_status.current_phase = "failed"
            execution_status.retry_count += 1
            
            self.execution_stats["failed_executions"] += 1
            
            # Re-raise for retry logic
            raise
        
        finally:
            # Update completion time and metrics
            execution_status.completed_at = datetime.now()
            execution_status.execution_time_seconds = (
                execution_status.completed_at - execution_status.started_at
            ).total_seconds()
            
            # Update average execution time
            if self.execution_stats["total_executions"] > 0:
                current_avg = self.execution_stats["average_execution_time"]
                total_executions = self.execution_stats["total_executions"]
                new_avg = (
                    (current_avg * (total_executions - 1) + execution_status.execution_time_seconds)
                    / total_executions
                )
                self.execution_stats["average_execution_time"] = new_avg
            
            self.execution_stats["concurrent_executions"] -= 1
            
            # Final progress update
            await self._publish_progress_update(execution_status)
    
    async def get_execution_status(self, execution_id: str) -> Optional[ExecutionStatus]:
        """Get current status of execution."""
        return self.active_executions.get(execution_id)
    
    async def get_execution_results(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get results of completed execution."""
        
        execution = self.active_executions.get(execution_id)
        if not execution:
            return None
        
        if execution.status == "completed":
            return execution.final_output
        elif execution.status == "failed":
            return {"error": "Execution failed", "details": execution.errors}
        else:
            return {"status": execution.status, "progress": execution.progress_percentage}
    
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
    
    async def list_active_executions(self) -> List[ExecutionStatus]:
        """List all active executions."""
        return [
            execution for execution in self.active_executions.values()
            if execution.status in ["pending", "running"]
        ]
    
    async def cleanup_completed_executions(self, max_age_hours: int = 24):
        """Clean up old completed executions."""
        
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        
        executions_to_remove = [
            execution_id for execution_id, execution in self.active_executions.items()
            if (execution.status in ["completed", "failed", "cancelled"] and 
                execution.completed_at and execution.completed_at < cutoff_time)
        ]
        
        for execution_id in executions_to_remove:
            del self.active_executions[execution_id]
        
        logger.info(f"Cleaned up {len(executions_to_remove)} old executions")
        
        return len(executions_to_remove)
    
    async def _publish_progress_update(self, execution_status: ExecutionStatus):
        """Publish progress update via WebSocket."""
        
        if not self.event_publisher:
            return
        
        try:
            progress_event = {
                "event_type": "execution_progress",
                "execution_id": execution_status.execution_id,
                "project_id": execution_status.project_id,
                "status": execution_status.status,
                "progress_percentage": execution_status.progress_percentage,
                "current_phase": execution_status.current_phase,
                "supervisor_type": execution_status.supervisor_type,
                "workers_used": execution_status.workers_used,
                "execution_time": execution_status.execution_time_seconds,
                "timestamp": datetime.now().isoformat(),
            }
            
            # Add errors if any
            if execution_status.errors:
                progress_event["errors"] = execution_status.errors
            
            # Add quality scores if available
            if execution_status.quality_scores:
                progress_event["quality_scores"] = execution_status.quality_scores
            
            await self.event_publisher.publish_project_event(
                execution_status.project_id,
                progress_event
            )
            
        except Exception as e:
            logger.warning(f"Failed to publish progress update: {e}")
    
    async def get_service_stats(self) -> Dict[str, Any]:
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
                    "duration": (datetime.now() - execution.started_at).total_seconds(),
                }
                for execution in self.active_executions.values()
                if execution.status in ["pending", "running"]
            ],
            "component_health": {
                "masr_router": "healthy" if self.masr_router else "unavailable",
                "supervisor_bridge": "healthy" if self.supervisor_bridge else "unavailable",
                "supervisor_factory": "healthy" if self.supervisor_factory else "unavailable",
            }
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on service components."""
        
        health = {
            "status": "healthy",
            "components": {},
            "active_executions": len(self.active_executions),
            "service_stats": self.execution_stats,
        }
        
        # Check MASR router health
        if self.masr_router:
            try:
                masr_health = await self.masr_router.health_check()
                health["components"]["masr_router"] = masr_health.get("status", "unknown")
            except Exception as e:
                health["components"]["masr_router"] = f"unhealthy: {e}"
        else:
            health["components"]["masr_router"] = "unavailable"
        
        # Check supervisor bridge health
        if self.supervisor_bridge:
            try:
                bridge_health = await self.supervisor_bridge.health_check()
                health["components"]["supervisor_bridge"] = bridge_health.get("status", "unknown")
            except Exception as e:
                health["components"]["supervisor_bridge"] = f"unhealthy: {e}"
        else:
            health["components"]["supervisor_bridge"] = "unavailable"
        
        # Check supervisor factory health
        if self.supervisor_factory:
            try:
                factory_health = await self.supervisor_factory.health_check()
                health["components"]["supervisor_factory"] = factory_health.get("status", "unknown")
            except Exception as e:
                health["components"]["supervisor_factory"] = f"unhealthy: {e}"
        else:
            health["components"]["supervisor_factory"] = "unavailable"
        
        # Determine overall health
        component_statuses = list(health["components"].values())
        if all("healthy" in status for status in component_statuses):
            health["status"] = "healthy"
        elif any("unhealthy" in status for status in component_statuses):
            health["status"] = "degraded"
        else:
            health["status"] = "unknown"
        
        return health


# Legacy compatibility functions for migration
async def create_research_plan(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy create_research_plan - should migrate to direct execution")
    
    return {
        "plan_created": True,
        "project_id": project_data.get("id"),
        "note": "Migrated from Temporal to direct execution",
        "timestamp": datetime.now().isoformat(),
    }


async def execute_agent_task(agent_task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy execute_agent_task - should use supervisor execution")
    
    return {
        "task_completed": True,
        "agent_type": agent_task_data.get("agent_type", "unknown"),
        "note": "Migrated from Temporal activity to supervisor execution",
        "timestamp": datetime.now().isoformat(),
    }


async def aggregate_results(results_data: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy aggregate_results - handled by supervisors now")
    
    return {
        "aggregation_completed": True,
        "results_count": len(results_data.get("results", [])),
        "note": "Migrated from Temporal activity to supervisor result aggregation",
        "timestamp": datetime.now().isoformat(),
    }


# Global service instance (would be properly injected in production)
_direct_execution_service: Optional[DirectExecutionService] = None


def get_direct_execution_service() -> DirectExecutionService:
    """Get global direct execution service instance."""
    global _direct_execution_service
    
    if _direct_execution_service is None:
        _direct_execution_service = DirectExecutionService()
    
    return _direct_execution_service


__all__ = [
    "DirectExecutionService",
    "ExecutionStatus", 
    "get_direct_execution_service",
    # Legacy compatibility exports
    "create_research_plan",
    "execute_agent_task", 
    "aggregate_results",
]