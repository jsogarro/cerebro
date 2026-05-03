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
