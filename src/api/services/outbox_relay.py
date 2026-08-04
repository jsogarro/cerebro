"""Drains the durable run-event outbox and delivers events at least once.

``agent_run_event_outbox`` rows are written in the same transaction as the
``agent_run_events`` row they describe (``RunEventRepository.append_event``),
so an outbox row is never visible for an event that didn't durably land
first. This module is purely a *delivery* mechanism on top of that already-
durable fact — Redis pub/sub, and whatever eventually subscribes to it, is
delivery, not the source of truth. The source of truth stays
``agent_run_events``.

Delivery is at-least-once, never exactly-once: a row that fails to deliver,
or a relay process that crashes between claiming a row and marking it
delivered, is retried on a later poll once its backoff window elapses.
Consumers must dedupe on ``idempotency_key`` (derived once, at event-append
time, from the event's ``deduplication_key`` and the destination — see
``src.core.contracts.delivery_idempotency_key``), which stays the same
across every redelivery attempt of the same event to the same destination.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from src.api.services.event_publisher import EventPublisher
from src.api.websocket.run_stream import RunStreamHub, run_stream_hub
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus

logger = get_logger()

_MAX_BACKOFF_SECONDS = 300


class OutboxRelay:
    """Polls ``agent_run_event_outbox`` and delivers claimable rows.

    Assumes a single relay instance per process. Concurrent relay instances
    across processes would race on claiming the same rows without corrupting
    data (claims are an atomic ``UPDATE ... WHERE status IN (...)``), but
    could both attempt delivery of the same row before either commits its
    claim — acceptable under at-least-once delivery, but not something this
    packet tunes for throughput. Multi-instance coordination (e.g. claiming
    with ``SKIP LOCKED``) is future work, not a correctness requirement here.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        event_publisher: EventPublisher,
        worker_id: str | None = None,
        poll_interval_seconds: float = 1.0,
        batch_size: int = 50,
        max_attempts: int = 10,
        stream_hub: RunStreamHub | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.event_publisher = event_publisher
        self.stream_hub = stream_hub if stream_hub is not None else run_stream_hub
        self.worker_id = worker_id or f"outbox-relay-{uuid.uuid4().hex[:8]}"
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        """Start the background polling loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        """Stop the polling loop and wait for any in-flight iteration."""
        self._stopped.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                delivered = await self.run_once()
                if delivered == 0:
                    await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("outbox_relay_iteration_failed", error=str(exc))
                await asyncio.sleep(self.poll_interval_seconds)

    async def run_once(self) -> int:
        """Claim and attempt delivery of one batch. Returns count delivered."""
        async with self.session_factory() as session:
            rows = await self._claim_batch(session)
            await session.commit()

        delivered = 0
        for row in rows:
            ok = await self._deliver(row)
            async with self.session_factory() as session:
                await self._finalize(session, row, delivered=ok)
                await session.commit()
            if ok:
                delivered += 1
        self._notify_stream_subscribers(rows)
        return delivered

    def _notify_stream_subscribers(self, rows: list[AgentRunEventOutbox]) -> None:
        """Wake local stream subscribers for every run this batch touched.

        Deliberately independent of whether a row's own delivery succeeded:
        the event is already durable in ``agent_run_events``, and subscribers
        re-read it from there rather than from anything this relay hands them.
        Rows with no organization are skipped — an unattributable row has no
        tenant to notify, and guessing one would be the leak this notification
        scheme exists to avoid.
        """
        notified: set[tuple[str, str]] = set()
        for row in rows:
            if row.organization_id is None:
                continue
            key = (str(row.organization_id), row.run_id)
            if key in notified:
                continue
            notified.add(key)
            self.stream_hub.notify(
                organization_id=row.organization_id, run_id=row.run_id
            )

    async def _claim_batch(self, session: AsyncSession) -> list[AgentRunEventOutbox]:
        """Atomically move claimable rows to ``in_flight`` and return them.

        Claimable means ``pending`` or a previously ``failed`` row whose
        backoff window (``available_at``) has elapsed. ``attempts`` is
        incremented as part of the same claim, so a row's attempt count
        always reflects delivery attempts actually made, not attempts
        merely scheduled.
        """
        now = datetime.now(UTC)
        select_stmt = (
            select(AgentRunEventOutbox.id)
            .where(
                AgentRunEventOutbox.status.in_(
                    (
                        EventDeliveryStatus.PENDING.value,
                        EventDeliveryStatus.FAILED.value,
                    )
                ),
                AgentRunEventOutbox.available_at <= now,
            )
            .order_by(AgentRunEventOutbox.id)
            .limit(self.batch_size)
        )
        ids = [row[0] for row in (await session.execute(select_stmt)).all()]
        if not ids:
            return []
        claim_stmt = (
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.id.in_(ids))
            .values(
                status=EventDeliveryStatus.IN_FLIGHT.value,
                claimed_at=now,
                claimed_by=self.worker_id,
                attempts=AgentRunEventOutbox.attempts + 1,
            )
            .returning(AgentRunEventOutbox)
        )
        result = await session.execute(claim_stmt)
        return list(result.scalars().all())

    async def _deliver(self, row: AgentRunEventOutbox) -> bool:
        if row.destination == "redis":
            envelope = row.payload or {}
            return await self.event_publisher.publish_run_event(
                run_id=row.run_id,
                event_type=envelope.get("event_type", ""),
                payload=envelope.get("payload", {}),
                occurred_at=envelope.get("occurred_at"),
            )
        if row.destination == "websocket":
            # Tenant-scoped by construction: the wakeup is keyed by the row's
            # own organization, and carries no payload. Subscribers re-read
            # the event through their own organization-filtered query, so a
            # notification can never deliver another tenant's data. A row with
            # no organization is unattributable and is left to retry.
            if row.organization_id is None:
                return False
            self.stream_hub.notify(
                organization_id=row.organization_id, run_id=row.run_id
            )
            return True
        logger.warning(
            "outbox_relay_unknown_destination",
            destination=row.destination,
            event_id=row.event_id,
        )
        return False

    async def _finalize(
        self, session: AsyncSession, row: AgentRunEventOutbox, *, delivered: bool
    ) -> None:
        now = datetime.now(UTC)
        if delivered:
            await session.execute(
                update(AgentRunEventOutbox)
                .where(AgentRunEventOutbox.id == row.id)
                .values(status=EventDeliveryStatus.DELIVERED.value, delivered_at=now)
            )
            return
        backoff_seconds = min(2**row.attempts, _MAX_BACKOFF_SECONDS)
        if row.destination == "redis":
            error = "redis publish unavailable or failed"
        elif row.destination == "websocket":
            error = "stream row has no organization to notify"
        else:
            error = f"unknown destination {row.destination!r}"
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.id == row.id)
            .values(
                status=EventDeliveryStatus.FAILED.value,
                available_at=now + timedelta(seconds=backoff_seconds),
                last_error=error,
            )
        )


__all__ = ["OutboxRelay"]
