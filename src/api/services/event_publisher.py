"""
Event publishing service for WebSocket real-time updates.

This service provides a centralized way to publish events that trigger
WebSocket notifications. It integrates with Redis pub/sub for scalability
across multiple server instances.
"""

import asyncio
import contextlib
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

    def __init__(self) -> None:
        self.redis_client: redis.Redis[bytes] | None = None
        self.redis_subscriber: redis.Redis[bytes] | None = None
        self.subscription_task: asyncio.Task[None] | None = None
        self._shutdown = False

    async def initialize(self) -> None:
        """Initialize Redis connections and start subscription."""
        # The module-level compatibility instance may cross sequential app
        # lifespans in tests and reloads. Reset terminal state and discard only
        # already-closed references before acquiring fresh clients.
        self._shutdown = False
        self.subscription_task = None
        self.redis_client = None
        self.redis_subscriber = None
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
            if self.redis_client:
                with contextlib.suppress(Exception):
                    await self.redis_client.close()
            if self.redis_subscriber:
                with contextlib.suppress(Exception):
                    await self.redis_subscriber.close()
            self.redis_client = None
            self.redis_subscriber = None
            self.subscription_task = None

    async def shutdown(self) -> None:
        """Shutdown the event publisher."""
        self._shutdown = True

        # Cancel subscription task
        subscription_task = self.subscription_task
        redis_client = self.redis_client
        redis_subscriber = self.redis_subscriber
        self.subscription_task = None
        self.redis_client = None
        self.redis_subscriber = None

        if subscription_task:
            subscription_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await subscription_task

        if redis_client:
            with contextlib.suppress(Exception):
                await redis_client.close()
        if redis_subscriber:
            with contextlib.suppress(Exception):
                await redis_subscriber.close()

        logger.info("Event publisher shutdown complete")

    async def publish_project_event(
        self, project_id: Any, event: dict[str, Any]
    ) -> None:
        """Publish a project-scoped event with an untyped dict payload.

        Generic compatibility shim absorbed from the deleted
        ``src/api/websocket/event_publisher.py`` stub. Logs only — does
        NOT broadcast over WebSocket. Behavior matches the original stub.

        **Migration:** new code should call one of the typed methods
        below, which both broadcast over WebSocket and publish to Redis:

        - progress updates → ``publish_progress_update(project_id, ProgressUpdate)``
        - agent lifecycle  → ``publish_agent_started`` / ``publish_agent_progress``
                             / ``publish_agent_completed`` / ``publish_agent_failed``
        - project lifecycle → ``publish_project_started`` / ``publish_project_completed``
                              / ``publish_project_failed`` / ``publish_project_cancelled``
        - workflow phases   → ``publish_workflow_phase_started``
                              / ``publish_workflow_phase_completed``

        Current callers to migrate:
        - ``DirectExecutionService.publish_progress_update`` — should call
          ``publish_progress_update`` with a typed ``ProgressUpdate`` model.
        """
        logger.debug(
            "Project event published (untyped)",
            project_id=str(project_id),
            keys=list(event.keys())
            if isinstance(event, dict)
            else type(event).__name__,
        )

    async def publish_event(
        self,
        event_type: str,
        data: dict[str, Any],
        target_clients: list[str] | None = None,
    ) -> None:
        """Publish a generic event, optionally targeted at specific clients.

        Generic compatibility shim absorbed from the deleted
        ``src/api/websocket/event_publisher.py`` stub. Logs only — does
        NOT actually deliver to ``target_clients``. Behavior matches the
        original stub.

        **Migration:** the targeted-delivery feature this shim implies
        is not yet implemented. Production-quality client targeting
        needs the websocket ``connection_manager`` — see ``_publish_event``
        below for the broadcast-to-project / broadcast-to-all pattern, and
        ``connection_manager.broadcast_to_user`` for per-user delivery.

        Current callers to migrate:
        - ``RealTimeDashboard._broadcast_to_dashboard`` — once the
          dashboard event type joins ``WSMessageType``, switch to
          ``_publish_event(WSMessage(type=..., data=data))`` with the
          correct broadcast scope.
        """
        target_str = (
            f" -> {len(target_clients)} client(s)" if target_clients else " (broadcast)"
        )
        logger.debug(f"Event published (untyped): {event_type}{target_str}")

    async def publish_progress_update(
        self,
        project_id: UUID,
        progress: ProgressUpdate,
        include_cli_format: bool = True,
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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

    async def publish_project_started(self, project_id: UUID) -> None:
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
    ) -> None:
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
    ) -> None:
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

    async def publish_project_cancelled(self, project_id: UUID) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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

    async def _publish_event(self, message: WSMessage) -> None:
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

    async def _redis_subscriber(self) -> None:
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
            await pubsub.close()

        except Exception as e:
            logger.error(
                "Redis subscription error",
                error=str(e),
            )


# Global event publisher instance
event_publisher = EventPublisher()
