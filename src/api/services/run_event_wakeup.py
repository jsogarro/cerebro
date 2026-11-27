"""Turn run-event Redis messages into tenant-scoped stream wakeups.

``EventPublisher.publish_run_event`` announces each durable run event on a
Redis channel so other API processes can wake their stream readers. This is the
consumer. A message is only a hint that run R advanced: it carries no authority,
so the organization to wake is read from the durable run row, never from the
envelope, and readers re-read the event log through their own tenant-filtered
query. Redis may drop, duplicate or reorder messages; the stream's poll
interval and cursor replay remain the source of truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.api.websocket.run_stream import RunStreamHub, run_stream_hub

from src.repositories.tenant_scope import get_run_organization_id

logger = get_logger()

RUN_EVENTS_CHANNEL = "research_platform:run_events"
_RECONNECT_DELAY_SECONDS = 1.0


class RunEventWakeupSubscriber:
    """Consume ``RUN_EVENTS_CHANNEL`` and notify the local ``RunStreamHub``."""

    def __init__(
        self,
        *,
        redis_client: Redis[bytes],
        session_factory: Callable[[], AsyncSession],
        stream_hub: RunStreamHub | None = None,
    ) -> None:
        self._redis = redis_client
        self._session_factory = session_factory
        self._hub = stream_hub if stream_hub is not None else run_stream_hub
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start consuming in the background. Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def handle_message(self, data: bytes | str) -> bool:
        """Wake the readers of the run an envelope names; return whether it did.

        Anything but a well-formed envelope for a run that exists with an
        organization is dropped without touching the database.
        """
        run_id = _run_id_of(data)
        if run_id is None:
            logger.warning("run_event_wakeup_malformed_envelope")
            return False
        try:
            async with self._session_factory() as session:
                organization_id = await get_run_organization_id(session, run_id)
        except SQLAlchemyError as error:
            logger.warning("run_event_wakeup_lookup_failed", error=str(error))
            return False
        if organization_id is None:
            logger.warning("run_event_wakeup_unknown_run", run_id=run_id)
            return False
        self._hub.notify(organization_id=organization_id, run_id=run_id)
        return True

    async def _run_forever(self) -> None:
        while True:
            try:
                async with self._redis.pubsub() as pubsub:
                    await pubsub.subscribe(RUN_EVENTS_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            await self.handle_message(message["data"])
            except (RedisError, OSError) as error:
                logger.warning("run_event_wakeup_redis_error", error=str(error))
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)


def _run_id_of(data: bytes | str) -> str | None:
    """Return the run id of a valid envelope, or ``None`` if it is not one."""
    try:
        envelope: Any = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(envelope, dict):
        return None
    run_id = envelope.get("run_id")
    sequence = envelope.get("sequence")
    if not isinstance(run_id, str) or not run_id:
        return None
    if sequence is not None and (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
    ):
        return None
    return run_id
