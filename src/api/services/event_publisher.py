"""
Event publishing service for WebSocket real-time updates.

This service provides a centralized way to publish events that trigger
WebSocket notifications. It integrates with Redis pub/sub for scalability
across multiple server instances.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

import redis.asyncio as redis
from structlog import get_logger

from src.api.websocket.connection_manager import websocket_manager
from src.core.config import settings
from src.models.websocket_messages import (
    AgentUpdate,
    ProgressUpdate,
    WorkflowPhaseUpdate,
    WSMessage,
    WSMessageType,
)

logger = get_logger()


class EventPublisher:
    """
    Centralized event publishing service.

    Publishes events both locally (to WebSocket connections) and to Redis
    for distribution across multiple server instances.
    """

    def __init__(self):
        self.redis_client: redis.Redis | None = None
        self.redis_subscriber: redis.Redis | None = None
        self.subscription_task: asyncio.Task | None = None
        self._shutdown = False

    async def initialize(self):
        """Initialize Redis connections and start subscription."""
        try:
            # Initialize Redis clients
            self.redis_client = redis.from_url(settings.REDIS_URL)
            self.redis_subscriber = redis.from_url(settings.REDIS_URL)

            # Test connections
            await self.redis_client.ping()
            await self.redis_subscriber.ping()

            # Start Redis subscription for distributed events
            self.subscription_task = asyncio.create_task(self._redis_subscriber())

            logger.info("Event publisher initialized with Redis support")

        except Exception as e:
            logger.warning(
                "Failed to initialize Redis for event publishing, using local-only mode",
                error=str(e),
            )
            self.redis_client = None
            self.redis_subscriber = None

    async def shutdown(self):
        """Shutdown the event publisher."""
        self._shutdown = True

        # Cancel subscription task
        if self.subscription_task:
            self.subscription_task.cancel()
            try:
                await self.subscription_task
            except asyncio.CancelledError:
                pass

        # Close Redis connections
        if self.redis_client:
            await self.redis_client.aclose()
        if self.redis_subscriber:
            await self.redis_subscriber.aclose()

        logger.info("Event publisher shutdown complete")

    async def publish_progress_update(
        self,
        project_id: UUID,
        progress: ProgressUpdate,
        include_cli_format: bool = True,
    ):
        """Publish a progress update event."""
        message = WSMessage(
            type=WSMessageType.PROGRESS,
            project_id=project_id,
            data=progress.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published progress update",
            project_id=str(project_id),
            progress_percentage=progress.progress_percentage,
        )

    async def publish_agent_started(
        self,
        project_id: UUID,
        agent_update: AgentUpdate,
    ):
        """Publish an agent started event."""
        message = WSMessage(
            type=WSMessageType.AGENT_STARTED,
            project_id=project_id,
            data=agent_update.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published agent started event",
            project_id=str(project_id),
            agent_type=agent_update.agent_type,
        )

    async def publish_agent_progress(
        self,
        project_id: UUID,
        agent_update: AgentUpdate,
    ):
        """Publish an agent progress event."""
        message = WSMessage(
            type=WSMessageType.AGENT_PROGRESS,
            project_id=project_id,
            data=agent_update.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published agent progress event",
            project_id=str(project_id),
            agent_type=agent_update.agent_type,
            progress=agent_update.progress_percentage,
        )

    async def publish_agent_completed(
        self,
        project_id: UUID,
        agent_update: AgentUpdate,
    ):
        """Publish an agent completed event."""
        message = WSMessage(
            type=WSMessageType.AGENT_COMPLETED,
            project_id=project_id,
            data=agent_update.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published agent completed event",
            project_id=str(project_id),
            agent_type=agent_update.agent_type,
        )

    async def publish_agent_failed(
        self,
        project_id: UUID,
        agent_update: AgentUpdate,
    ):
        """Publish an agent failed event."""
        message = WSMessage(
            type=WSMessageType.AGENT_FAILED,
            project_id=project_id,
            data=agent_update.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published agent failed event",
            project_id=str(project_id),
            agent_type=agent_update.agent_type,
            error=agent_update.error_message,
        )

    async def publish_workflow_phase_started(
        self,
        project_id: UUID,
        phase_update: WorkflowPhaseUpdate,
    ):
        """Publish a workflow phase started event."""
        message = WSMessage(
            type=WSMessageType.WORKFLOW_PHASE_STARTED,
            project_id=project_id,
            data=phase_update.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published workflow phase started event",
            project_id=str(project_id),
            phase=phase_update.phase_name,
        )

    async def publish_workflow_phase_completed(
        self,
        project_id: UUID,
        phase_update: WorkflowPhaseUpdate,
    ):
        """Publish a workflow phase completed event."""
        message = WSMessage(
            type=WSMessageType.WORKFLOW_PHASE_COMPLETED,
            project_id=project_id,
            data=phase_update.model_dump(),
        )

        await self._publish_event(message)

        logger.debug(
            "Published workflow phase completed event",
            project_id=str(project_id),
            phase=phase_update.phase_name,
        )

    async def publish_project_started(self, project_id: UUID):
        """Publish a project started event."""
        message = WSMessage(
            type=WSMessageType.PROJECT_STARTED,
            project_id=project_id,
            data={"message": f"Research project {project_id} started"},
        )

        await self._publish_event(message)

        logger.info(
            "Published project started event",
            project_id=str(project_id),
        )

    async def publish_project_completed(
        self,
        project_id: UUID,
        results_summary: str | None = None,
    ):
        """Publish a project completed event."""
        message = WSMessage(
            type=WSMessageType.PROJECT_COMPLETED,
            project_id=project_id,
            data={
                "message": f"Research project {project_id} completed",
                "results_summary": results_summary,
            },
        )

        await self._publish_event(message)

        logger.info(
            "Published project completed event",
            project_id=str(project_id),
        )

    async def publish_project_failed(
        self,
        project_id: UUID,
        error_message: str,
    ):
        """Publish a project failed event."""
        message = WSMessage(
            type=WSMessageType.PROJECT_FAILED,
            project_id=project_id,
            data={
                "message": f"Research project {project_id} failed",
                "error_message": error_message,
            },
        )

        await self._publish_event(message)

        logger.error(
            "Published project failed event",
            project_id=str(project_id),
            error=error_message,
        )

    async def publish_project_cancelled(self, project_id: UUID):
        """Publish a project cancelled event."""
        message = WSMessage(
            type=WSMessageType.PROJECT_CANCELLED,
            project_id=project_id,
            data={"message": f"Research project {project_id} cancelled"},
        )

        await self._publish_event(message)

        logger.info(
            "Published project cancelled event",
            project_id=str(project_id),
        )

    async def publish_error(
        self,
        project_id: UUID | None,
        error_message: str,
        details: dict[str, Any] | None = None,
    ):
        """Publish an error event."""
        message = WSMessage(
            type=WSMessageType.ERROR,
            project_id=project_id,
            data={
                "message": error_message,
                "details": details or {},
            },
        )

        await self._publish_event(message)

        logger.error(
            "Published error event",
            project_id=str(project_id) if project_id else None,
            error=error_message,
        )

    async def publish_warning(
        self,
        project_id: UUID | None,
        warning_message: str,
        details: dict[str, Any] | None = None,
    ):
        """Publish a warning event."""
        message = WSMessage(
            type=WSMessageType.WARNING,
            project_id=project_id,
            data={
                "message": warning_message,
                "details": details or {},
            },
        )

        await self._publish_event(message)

        logger.warning(
            "Published warning event",
            project_id=str(project_id) if project_id else None,
            warning=warning_message,
        )

    async def publish_info(
        self,
        project_id: UUID | None,
        info_message: str,
        details: dict[str, Any] | None = None,
    ):
        """Publish an info event."""
        message = WSMessage(
            type=WSMessageType.INFO,
            project_id=project_id,
            data={
                "message": info_message,
                "details": details or {},
            },
        )

        await self._publish_event(message)

        logger.info(
            "Published info event",
            project_id=str(project_id) if project_id else None,
            message=info_message,
        )

    async def _publish_event(self, message: WSMessage):
        """
        Publish an event both locally and to Redis.

        Args:
            message: WebSocket message to publish
        """
        # Publish locally to WebSocket connections
        if message.project_id:
            await websocket_manager.broadcast_to_project(message.project_id, message)
        else:
            await websocket_manager.broadcast_to_all(message)

        # Publish to Redis for distribution across instances
        if self.redis_client:
            try:
                await self.redis_client.publish(
                    "research_platform:events",
                    message.model_dump_json(),
                )
            except Exception as e:
                logger.warning(
                    "Failed to publish event to Redis",
                    error=str(e),
                    message_type=message.type,
                )

    async def _redis_subscriber(self):
        """Background task to handle Redis pub/sub events from other instances."""
        if not self.redis_subscriber:
            return

        try:
            pubsub = self.redis_subscriber.pubsub()
            await pubsub.subscribe("research_platform:events")

            logger.info("Started Redis event subscription")

            while not self._shutdown:
                try:
                    # Get message with timeout
                    message = await pubsub.get_message(timeout=1.0)

                    if message and message["type"] == "message":
                        # Parse and handle event
                        event_data = json.loads(message["data"])
                        ws_message = WSMessage(**event_data)

                        # Broadcast to local WebSocket connections
                        # (Skip Redis publishing to avoid loops)
                        if ws_message.project_id:
                            await websocket_manager.broadcast_to_project(
                                ws_message.project_id, ws_message
                            )
                        else:
                            await websocket_manager.broadcast_to_all(ws_message)

                        logger.debug(
                            "Received and broadcasted Redis event",
                            message_type=ws_message.type,
                            project_id=(
                                str(ws_message.project_id)
                                if ws_message.project_id
                                else None
                            ),
                        )

                except TimeoutError:
                    # Timeout is expected
                    continue

                except Exception as e:
                    logger.error(
                        "Error processing Redis event",
                        error=str(e),
                    )
                    await asyncio.sleep(1)  # Brief pause before retrying

            await pubsub.unsubscribe("research_platform:events")
            await pubsub.aclose()

        except Exception as e:
            logger.error(
                "Redis subscription error",
                error=str(e),
            )


# Global event publisher instance
event_publisher = EventPublisher()
