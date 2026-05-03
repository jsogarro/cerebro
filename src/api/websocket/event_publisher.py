"""
WebSocket Event Publisher.

Publishes real-time events to connected WebSocket clients
during research execution.
"""

from typing import Any

from structlog import get_logger

logger = get_logger()


class EventPublisher:
    """Publishes execution events to WebSocket connections."""

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to connected clients."""
        logger.debug(f"Event published: {event_type}")

    async def publish_progress(
        self, execution_id: str, progress: float, message: str = ""
    ) -> None:
        """Publish a progress update."""
        logger.debug(f"Progress {execution_id}: {progress:.0%} {message}")

    async def publish_error(self, execution_id: str, error: str) -> None:
        """Publish an error event."""
        logger.warning(f"Error event {execution_id}: {error}")

    async def publish_project_event(
        self, project_id: Any, event: dict[str, Any]
    ) -> None:
        """Publish a generic project-scoped event.

        Used by DirectExecutionService to broadcast progress/status snapshots
        keyed by project_id. The stub logs only; the production publisher at
        src.api.services.event_publisher.EventPublisher offers strictly-typed
        per-event-type methods (publish_progress_update, publish_agent_*, etc.)
        that callers should migrate to once the WebSocket connection-manager
        wiring is consolidated.
        """
        logger.debug(
            f"Project event published: project_id={project_id} "
            f"keys={list(event.keys()) if isinstance(event, dict) else type(event).__name__}"
        )

    async def publish_event(
        self,
        event_type: str,
        data: dict[str, Any],
        target_clients: list[str] | None = None,
    ) -> None:
        """Publish a generic event, optionally targeted at specific WebSocket clients.

        Used by RealTimeDashboard._broadcast_to_dashboard to push dashboard_update
        events to per-client subscriptions. The stub logs only; targeted delivery
        requires the WebSocket connection_manager (see RealTimeDashboard for the
        production wiring pattern).
        """
        target_str = (
            f" -> {len(target_clients)} client(s)"
            if target_clients
            else " (broadcast)"
        )
        logger.debug(f"Event published: {event_type}{target_str}")
