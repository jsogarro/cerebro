"""
Workflow service for managing research workflows through Temporal.

This service provides the interface between the API and Temporal workflows.
Enhanced with WebSocket event publishing for real-time updates.
"""

import logging
from typing import Any
from uuid import UUID

from src.temporal.client import TemporalResearchClient

from src.api.services.event_publisher import event_publisher
from src.models.research_project import ResearchProject, ResearchStatus
from src.models.websocket_messages import (
    ProgressUpdate,
)

logger = logging.getLogger(__name__)


class WorkflowService:
    """
    Service for managing research workflows.

    This service follows functional principles by treating workflows
    as pure data transformations.
    """

    def __init__(self, temporal_host: str = "localhost:7233"):
        """
        Initialize the workflow service.

        Args:
            temporal_host: Temporal server address
        """
        self.temporal_client = TemporalResearchClient(temporal_host=temporal_host)

    async def start_research(self, project: ResearchProject) -> str:
        """
        Start a research workflow for a project.

        This initiates the transformation pipeline:
        ResearchProject -> Temporal Workflow -> Research Results

        Args:
            project: The research project to execute

        Returns:
            The workflow ID
        """
        logger.info(f"Starting research workflow for project: {project.id}")

        try:
            # Start the workflow with the project ID as the workflow ID
            workflow_id = await self.temporal_client.start_research_workflow(
                project=project,
                workflow_id=str(project.id),
            )

            logger.info(f"Workflow started with ID: {workflow_id}")

            # Publish project started event
            await event_publisher.publish_project_started(project.id)

            # Publish initial progress update
            initial_progress = ProgressUpdate(
                total_tasks=5,  # Typical number of research agents
                completed_tasks=0,
                pending_tasks=5,
                progress_percentage=0.0,
                current_phase="initialization",
            )
            await event_publisher.publish_progress_update(project.id, initial_progress)

            return str(workflow_id)

        except Exception as e:
            await event_publisher.publish_error(
                project.id,
                f"Failed to start research workflow: {e!s}",
                {"workflow_id": str(project.id)},
            )
            raise

    async def get_progress(self, project_id: UUID) -> dict[str, Any]:
        """
        Get the progress of a research workflow and publish updates.

        Args:
            project_id: The project/workflow ID

        Returns:
            Progress information
        """
        try:
            workflow_status = await self.temporal_client.get_workflow_status(
                str(project_id)
            )

            # Extract progress from workflow status
            progress = workflow_status.get("progress", {})

            # Create progress update object
            progress_update = ProgressUpdate(
                total_tasks=len(progress.get("completed_tasks", []))
                + len(progress.get("pending_tasks", [])),
                completed_tasks=len(progress.get("completed_tasks", [])),
                failed_tasks=len(progress.get("failed_tasks", [])),
                in_progress_tasks=(
                    1
                    if progress.get("current_phase") not in ["completed", "failed"]
                    else 0
                ),
                pending_tasks=len(progress.get("pending_tasks", [])),
                progress_percentage=progress.get("percentage", 0),
                current_agent=progress.get("current_agent"),
                current_phase=progress.get("current_phase", "unknown"),
            )

            # Publish progress update (non-blocking)
            try:
                await event_publisher.publish_progress_update(
                    project_id, progress_update
                )
            except Exception as e:
                logger.warning(f"Failed to publish progress update: {e}")

            return {
                "project_id": project_id,
                "workflow_status": workflow_status.get("status"),
                "progress_percentage": progress.get("percentage", 0),
                "current_phase": progress.get("current_phase", "unknown"),
                "completed_tasks": progress.get("completed_tasks", []),
                "pending_tasks": progress.get("pending_tasks", []),
                "failed_tasks": progress.get("failed_tasks", []),
            }

        except Exception as e:
            logger.error(f"Failed to get workflow progress: {e}")
            # Publish error event
            await event_publisher.publish_error(
                project_id,
                f"Failed to get workflow progress: {e!s}",
            )
            raise

    async def cancel_research(self, project_id: UUID) -> bool:
        """
        Cancel a research workflow and publish cancellation event.

        Args:
            project_id: The project/workflow ID to cancel

        Returns:
            True if cancelled successfully
        """
        logger.info(f"Cancelling workflow for project: {project_id}")

        try:
            success = await self.temporal_client.cancel_workflow(str(project_id))

            if success:
                logger.info(f"Workflow cancelled: {project_id}")
                # Publish cancellation event
                await event_publisher.publish_project_cancelled(project_id)
            else:
                logger.error(f"Failed to cancel workflow: {project_id}")
                await event_publisher.publish_error(
                    project_id,
                    "Failed to cancel research workflow",
                )

            return bool(success)

        except Exception as e:
            logger.error(f"Error cancelling workflow: {e}")
            await event_publisher.publish_error(
                project_id,
                f"Error cancelling workflow: {e!s}",
            )
            return False

    async def get_results(self, project_id: UUID) -> dict[str, Any] | None:
        """
        Get the results of a completed research workflow.

        Args:
            project_id: The project/workflow ID

        Returns:
            The research results or None if not complete
        """
        try:
            results = await self.temporal_client.get_workflow_result(
                str(project_id),
                timeout=5,  # 5 second timeout for checking
            )

            if results:
                # Publish completion event with results summary
                results_summary = self._extract_results_summary(results)
                await event_publisher.publish_project_completed(
                    project_id,
                    results_summary,
                )

            return dict[str, Any](results) if results else None
        except Exception as e:
            logger.warning(f"Workflow not complete or failed: {e}")
            return None

    def _extract_results_summary(self, results: dict[str, Any]) -> str:
        if not results:
            return "Research completed"

        if "summary" in results:
            return str(results["summary"])[:200]
        elif "conclusion" in results:
            return str(results["conclusion"])[:200]
        elif "findings" in results:
            findings = results["findings"]
            if isinstance(findings, list) and findings:
                return f"Found {len(findings)} key findings"
            elif isinstance(findings, str):
                return findings[:200]

        return f"Research completed with {len(results)} result sections"

    async def list_active_workflows(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        List all active research workflows.

        Args:
            limit: Maximum number of workflows to return

        Returns:
            List of active workflows
        """
        # Query for running workflows
        workflows = await self.temporal_client.list_workflows(
            query="ExecutionStatus='Running'",
            limit=limit,
        )

        return list[dict[str, Any]](workflows)

    async def get_workflow_status(self, project_id: UUID) -> ResearchStatus:
        """
        Map Temporal workflow status to ResearchStatus.

        Args:
            project_id: The project/workflow ID

        Returns:
            The research status
        """
        workflow_status = await self.temporal_client.get_workflow_status(
            str(project_id)
        )

        temporal_status = workflow_status.get("status", "unknown").lower()

        # Map Temporal status to ResearchStatus
        status_mapping = {
            "running": ResearchStatus.IN_PROGRESS,
            "completed": ResearchStatus.COMPLETED,
            "failed": ResearchStatus.FAILED,
            "cancelled": ResearchStatus.CANCELLED,
            "terminated": ResearchStatus.CANCELLED,
            "continued_as_new": ResearchStatus.IN_PROGRESS,
            "timed_out": ResearchStatus.FAILED,
        }

        return status_mapping.get(temporal_status, ResearchStatus.PENDING)


# Singleton instance
workflow_service = WorkflowService()
