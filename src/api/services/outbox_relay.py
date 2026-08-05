"""Drains the durable run-event outbox and delivers events at least once.

``agent_run_event_outbox`` rows are written in the same transaction as the
``agent_run_events`` row they describe (``RunEventRepository.append_event``),
so an outbox row is never visible for an event that didn't durably land
first. This module is purely a *delivery* mechanism on top of that already-
durable fact — Redis pub/sub, and whatever eventually subscribes to it, is
delivery, not the source of truth. The source of truth stays
``agent_run_events``.

Delivery is at-least-once, never exactly-once: a row that fails to deliver
is retried on a later poll once its backoff window elapses, and a row whose
claim was abandoned — the relay process died between committing the claim
and finalizing the row — is reclaimed once its claim lease expires. Both
paths can hand a consumer the same event twice; that is the expected cost of
at-least-once. Consumers must dedupe on ``idempotency_key`` (derived once,
at event-append time, from the event's ``deduplication_key`` and the
destination — see ``src.core.contracts.delivery_idempotency_key``), which
stays the same across every redelivery attempt of the same event to the same
destination.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from src.api.services.event_publisher import EventPublisher
from src.api.websocket.run_stream import RunStreamHub, run_stream_hub
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus

logger = get_logger()

_MAX_BACKOFF_SECONDS = 300
_DEFAULT_CLAIM_LEASE_SECONDS = 60.0


class OutboxRelay:
    """Polls ``agent_run_event_outbox`` and delivers claimable rows.

    Assumes a single relay instance per process. Concurrent relay instances
    across processes would race on claiming the same rows without corrupting
    data (claims are an atomic ``UPDATE ... WHERE status IN (...)``), but
    could both attempt delivery of the same row before either commits its
    claim — acceptable under at-least-once delivery, but not something this
    packet tunes for throughput. Multi-instance coordination (e.g. claiming
    with ``SKIP LOCKED``) is future work, not a correctness requirement here.

    A claim is a *lease*, not a permanent assignment: a row left ``in_flight``
    by a relay that died mid-batch becomes claimable again once
    ``claim_lease_seconds`` have passed since ``claimed_at``, by this instance
    or by any other one sharing the database. Without that, an abandoned claim
    would never be delivered by anything, silently — the one failure mode a
    transactional outbox exists to rule out. The lease must be longer than a
    healthy delivery attempt takes, or a live relay's own rows get reclaimed
    underneath it; that costs a duplicate delivery, not correctness, since
    consumers dedupe on ``idempotency_key``.

    ``max_attempts`` bounds a row's total claims (ordinary failures and
    lease-expiry reclaims both count): once a row's ``attempts`` reaches it,
    ``_claim_batch`` never claims it again and ``_finalize`` marks it
    dead-lettered. A dead-lettered row is still ``status=failed`` — there is
    no separate dead-letter status or table — distinguished only by
    ``last_error`` naming it permanently dropped and by never again matching
    the claim query. Without this bound, a permanently undeliverable row
    (e.g. a destination that never comes back) retried forever at the
    ``_MAX_BACKOFF_SECONDS`` ceiling, silently, with nothing to page an
    operator or say the event was given up on.
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
        claim_lease_seconds: float = _DEFAULT_CLAIM_LEASE_SECONDS,
        stream_hub: RunStreamHub | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.event_publisher = event_publisher
        self.stream_hub = stream_hub if stream_hub is not None else run_stream_hub
        self.worker_id = worker_id or f"outbox-relay-{uuid.uuid4().hex[:8]}"
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.claim_lease_seconds = claim_lease_seconds
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

        A row is claimable when it is ``pending`` or previously ``failed``
        and its backoff window (``available_at``) has elapsed, or when it is
        ``in_flight`` under an expired claim lease — an abandoned claim from a
        relay that died before finalizing it — **and**, in either case, it has
        not already exhausted ``max_attempts`` (see ``_finalize``'s
        dead-letter path). ``attempts`` is incremented as part of the same
        claim, so a row's attempt count always reflects delivery attempts
        actually made, not attempts merely scheduled, and a reclaimed row's
        backoff keeps growing rather than restarting.

        An ``in_flight`` row with no ``claimed_at`` is treated as expired:
        this relay always stamps ``claimed_at`` in the same statement that
        sets ``in_flight``, so such a row can only come from outside this
        code path, and leaving it unclaimable would reopen the very hole the
        lease closes.
        """
        now = datetime.now(UTC)
        lease_expires_before = now - timedelta(seconds=self.claim_lease_seconds)
        claimable = and_(
            # A row that has already used up its allotted attempts is
            # dead-lettered by ``_finalize`` (see there) and must never be
            # claimed again, whether it is sitting ``failed`` or was abandoned
            # ``in_flight`` by a dead relay — either way, more attempts than
            # ``max_attempts`` is the one thing this bound exists to prevent.
            AgentRunEventOutbox.attempts < self.max_attempts,
            or_(
                and_(
                    AgentRunEventOutbox.status.in_(
                        (
                            EventDeliveryStatus.PENDING.value,
                            EventDeliveryStatus.FAILED.value,
                        )
                    ),
                    AgentRunEventOutbox.available_at <= now,
                ),
                and_(
                    AgentRunEventOutbox.status == EventDeliveryStatus.IN_FLIGHT.value,
                    or_(
                        AgentRunEventOutbox.claimed_at.is_(None),
                        AgentRunEventOutbox.claimed_at <= lease_expires_before,
                    ),
                ),
            ),
        )
        select_stmt = (
            select(AgentRunEventOutbox.id)
            .where(claimable)
            .order_by(AgentRunEventOutbox.id)
            .limit(self.batch_size)
        )
        ids = [row[0] for row in (await session.execute(select_stmt)).all()]
        if not ids:
            return []
        # Re-check claimability inside the UPDATE so a row another relay
        # claimed between the select and here is not stolen from it: only the
        # rows this statement actually transitions come back from RETURNING.
        claim_stmt = (
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.id.in_(ids), claimable)
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
            # ``row.payload`` is the full stored ``RunEvent`` envelope
            # (``RunEventRepository.append_event`` dumps it verbatim), so
            # ``event_id``/``sequence`` are already present here — no join,
            # no recomputation. ``row.idempotency_key`` is the same value a
            # consumer must dedupe redeliveries on; it is a column on this
            # row, not derived again, so it is guaranteed to match what the
            # transactional outbox actually recorded.
            envelope = row.payload or {}
            return await self.event_publisher.publish_run_event(
                run_id=row.run_id,
                event_type=envelope.get("event_type", ""),
                payload=envelope.get("payload", {}),
                occurred_at=envelope.get("occurred_at"),
                event_id=row.event_id,
                sequence=int(envelope.get("sequence", 0)),
                idempotency_key=row.idempotency_key,
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
        if row.destination == "redis":
            error = "redis publish unavailable or failed"
        elif row.destination == "websocket":
            error = "stream row has no organization to notify"
        else:
            error = f"unknown destination {row.destination!r}"
        if row.attempts >= self.max_attempts:
            # Dead-letter: an explicit, observable terminal state built out
            # of existing columns rather than a new status value or column.
            # ``status`` stays the already-permitted ``failed`` (the DB
            # CHECK constraint on this table only allows the four values
            # frozen in the Wave 3 migration), and ``_claim_batch``'s
            # ``attempts < max_attempts`` bound means this row can never be
            # claimed again — no dead-letter queue, no give-up before this,
            # just permanent silent retries against a ceiling that was
            # configured but never enforced.
            await session.execute(
                update(AgentRunEventOutbox)
                .where(AgentRunEventOutbox.id == row.id)
                .values(
                    status=EventDeliveryStatus.FAILED.value,
                    last_error=(
                        f"dead-lettered after {row.attempts} attempts "
                        f"(max_attempts={self.max_attempts}): {error}"
                    ),
                )
            )
            logger.error(
                "outbox_relay_dead_lettered",
                event_id=row.event_id,
                run_id=row.run_id,
                destination=row.destination,
                attempts=row.attempts,
                max_attempts=self.max_attempts,
            )
            return
        backoff_seconds = min(2**row.attempts, _MAX_BACKOFF_SECONDS)
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
