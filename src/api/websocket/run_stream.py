"""Derives live run updates from the persisted ``agent_run_events`` stream.

Everything a stream client sees is read back out of ``agent_run_events``,
never handed over from the process that produced it. That inversion is what
makes the stream resumable: a client that disconnects holds an opaque cursor
(``RunEventCursor``), and on reconnect the server replays exactly the events
after that sequence, in order, straight from the durable log.

Tenant boundary
---------------
A subscriber's entitlement is its authenticated ``organization_id`` claim, and
nothing else. ``AgentRun`` carries no project reference, so there is no
project-level scope available here; the accepted Wave 3 tenant contract makes
``organization_id`` the boundary and the repository's org filter its real
enforcement. Two consequences are load-bearing:

* Every read of the event log passes ``organization_id`` into
  ``RunEventRepository.read_events_after``, so the SQL predicate is applied on
  every page rather than once at subscribe time.
* The cursor is a *position*, not a capability. It carries only a run id and a
  sequence number. Entitlement is re-derived from the caller's token on every
  connection, including resumes, so a captured or forged cursor cannot widen
  access — a run outside the caller's organization is indistinguishable from a
  run that does not exist.

The live path never carries payloads. ``RunStreamHub`` is a wakeup signal
keyed by ``(organization_id, run_id)``: it tells a waiting subscriber that new
rows exist, and the subscriber then re-reads through its own org-filtered
query. Event bytes only ever reach a client through a query already scoped to
that client's organization.

Delivery is at-least-once. A reconnect, a relay retry, or a redelivery after a
crash can all present the same event twice. Clients must dedupe on the
``idempotency_key`` carried on each frame — see ``delivery_idempotency_key``.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from src.core.contracts import RunEventCursor, delivery_idempotency_key
from src.core.contracts.states import TERMINAL_RUN_STATUSES
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

logger = get_logger()

DEFAULT_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_BATCH_LIMIT = 100


class RunStreamAuthorizationError(Exception):
    """The caller has no usable tenant claim, so no stream can be opened."""


class RunNotVisibleError(Exception):
    """The run does not exist, or does not belong to the caller's tenant.

    Deliberately one error for both cases: distinguishing them would let a
    caller probe for the existence of another tenant's runs.
    """


@dataclass(frozen=True)
class RunStreamEntitlement:
    """What a stream subscriber is allowed to see.

    ``organization_id`` is the whole boundary. It is derived from the
    authenticated token's tenant claim, never from a path parameter, query
    string, or resume cursor.
    """

    organization_id: uuid.UUID
    user_id: str | None = None

    @classmethod
    def from_organization_claim(
        cls, claim: str | None, *, user_id: str | None = None
    ) -> Self:
        """Build an entitlement from a token's organization claim.

        Fails closed: a missing, blank, or non-UUID claim raises rather than
        degrading to an unscoped stream.
        """
        if claim is None or not claim.strip():
            raise RunStreamAuthorizationError(
                "Tenant organization claim is required to stream run events"
            )
        try:
            organization_id = uuid.UUID(claim.strip())
        except ValueError as error:
            raise RunStreamAuthorizationError(
                "Tenant organization claim is not a valid organization identifier"
            ) from error
        return cls(organization_id=organization_id, user_id=user_id)


class RunStreamHub:
    """In-process wakeup signal for subscribers tailing the event log.

    Holds no payloads and grants no access: a notification only says "run R in
    organization O has advanced". Subscribers respond by re-reading through
    their own org-filtered query, so this hub cannot become a cross-tenant
    delivery path even if it were notified incorrectly.

    Notifications are in-process only. A subscriber connected to a different
    API process is not woken by this hub and instead advances on its poll
    interval, which is why the stream must never depend on a notification
    arriving.
    """

    def __init__(self) -> None:
        self._waiters: dict[tuple[str, str], set[asyncio.Event]] = {}

    @property
    def waiter_count(self) -> int:
        """Total registered waiters, for tests and health reporting."""
        return sum(len(events) for events in self._waiters.values())

    def notify(self, *, organization_id: uuid.UUID | str, run_id: str) -> int:
        """Wake every subscriber of this exact ``(organization, run)`` pair.

        Returns the number of waiters woken.
        """
        waiters = self._waiters.get(self._key(organization_id, run_id))
        if not waiters:
            return 0
        for event in waiters:
            event.set()
        return len(waiters)

    @contextlib.asynccontextmanager
    async def subscribe(
        self, *, organization_id: uuid.UUID | str, run_id: str
    ) -> AsyncIterator[asyncio.Event]:
        """Register a waiter for one run, and always deregister it on exit."""
        key = self._key(organization_id, run_id)
        event = asyncio.Event()
        self._waiters.setdefault(key, set()).add(event)
        try:
            yield event
        finally:
            waiters = self._waiters.get(key)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    del self._waiters[key]

    @staticmethod
    def _key(organization_id: uuid.UUID | str, run_id: str) -> tuple[str, str]:
        return (str(organization_id), run_id)


run_stream_hub = RunStreamHub()
"""Process-wide hub shared by the outbox relay and the stream endpoints."""


@dataclass(frozen=True)
class StreamFrame:
    """One event as a client sees it, with its resume position and dedupe key."""

    run_id: str
    sequence: int
    event_id: str
    event_type: str
    occurred_at: str | datetime | None
    payload: dict[str, Any]
    cursor: RunEventCursor
    destination: str
    deduplication_key: str

    def to_message(self) -> dict[str, Any]:
        """Return the wire form of this frame.

        ``cursor`` is the token the client sends back to resume from exactly
        here; ``idempotency_key`` is what the client dedupes redeliveries on.
        """
        occurred_at = self.occurred_at
        return {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": (
                occurred_at.isoformat()
                if isinstance(occurred_at, datetime)
                else occurred_at
            ),
            "payload": self.payload,
            "cursor": self.cursor.encode(),
            "idempotency_key": delivery_idempotency_key(
                deduplication_key=self.deduplication_key,
                destination=self.destination,
            ),
        }


class RunEventStreamReader:
    """Reads one run's persisted events on behalf of one entitled subscriber.

    Each batch runs in its own short-lived session. The reader waits for new
    events *outside* any transaction, so tailing a quiet run never pins a
    database connection or holds a row lock against the writer.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        entitlement: RunStreamEntitlement,
        destination: str,
        hub: RunStreamHub | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
    ) -> None:
        self.session_factory = session_factory
        self.entitlement = entitlement
        self.destination = destination
        self.hub = hub if hub is not None else run_stream_hub
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_limit = batch_limit

    async def authorize(self, run_id: str) -> None:
        """Confirm the run exists *within the caller's organization*.

        Raises ``RunNotVisibleError`` for both "no such run" and "another
        tenant's run", so the two are indistinguishable to a caller.
        """
        async with self.session_factory() as session:
            run = await RunLifecycleRepository(session).get_run(
                run_id, organization_id=self.entitlement.organization_id
            )
        if run is None:
            raise RunNotVisibleError(f"run {run_id!r} is not visible to this tenant")

    async def read_batch(
        self, cursor: RunEventCursor
    ) -> tuple[list[StreamFrame], RunEventCursor]:
        """Return the next page of events after ``cursor``, and the new cursor.

        The organization filter is applied to this query, not inherited from
        an earlier authorization, so every page is independently tenant-scoped.
        """
        async with self.session_factory() as session:
            rows = await RunEventRepository(session).read_events_after(
                cursor.run_id,
                after_sequence=cursor.last_sequence,
                limit=self.batch_limit,
                organization_id=self.entitlement.organization_id,
            )

        frames: list[StreamFrame] = []
        advanced = cursor
        for row in rows:
            advanced = advanced.advanced_to(row.sequence)
            frames.append(
                StreamFrame(
                    run_id=row.run_id,
                    sequence=row.sequence,
                    event_id=row.event_id,
                    event_type=row.event_type,
                    occurred_at=row.occurred_at,
                    payload=dict(row.payload or {}),
                    cursor=advanced,
                    destination=self.destination,
                    deduplication_key=row.deduplication_key,
                )
            )
        return frames, advanced

    async def is_run_terminal(self, run_id: str) -> bool:
        """Whether the run has reached a terminal status, within this tenant."""
        async with self.session_factory() as session:
            run = await RunLifecycleRepository(session).get_run(
                run_id, organization_id=self.entitlement.organization_id
            )
        if run is None:
            return True
        return run.status in {status.value for status in TERMINAL_RUN_STATUSES}

    async def stream(
        self,
        run_id: str,
        cursor: RunEventCursor | None = None,
        *,
        stop_when_terminal: bool = True,
        max_idle_seconds: float | None = None,
    ) -> AsyncIterator[StreamFrame]:
        """Yield every event after ``cursor``, then tail the run until it ends.

        A caller that supplies a cursor from a previous connection receives
        exactly the events it missed, in sequence order, with no gap: the
        durable log — not any in-memory buffer — is what is replayed.

        ``max_idle_seconds`` bounds how long the tail waits with no new events
        before returning, so a caller is never blocked indefinitely on a run
        that stopped emitting without reaching a terminal status.
        """
        await self.authorize(run_id)
        position = cursor if cursor is not None else RunEventCursor(run_id=run_id)
        if position.run_id != run_id:
            raise RunNotVisibleError(
                f"resume cursor is for run {position.run_id!r}, not {run_id!r}"
            )

        idle_seconds = 0.0
        async with self.hub.subscribe(
            organization_id=self.entitlement.organization_id, run_id=run_id
        ) as waiter:
            while True:
                frames, position = await self.read_batch(position)
                for frame in frames:
                    yield frame

                if frames:
                    idle_seconds = 0.0
                    # A full page probably means more is already durable;
                    # drain before waiting on anything.
                    if len(frames) >= self.batch_limit:
                        continue
                elif stop_when_terminal and await self.is_run_terminal(run_id):
                    # `_persist_transition` commits a run's terminal event and
                    # its terminal status together, in one transaction. If
                    # that commit landed in the window between the batch read
                    # above (which found nothing) and this terminal check,
                    # the terminal event would otherwise never be read: this
                    # check only observes `True` once its commit is already
                    # visible, so one more read is guaranteed to pick up
                    # everything that commit wrote, including the event that
                    # made the run terminal, before this generator stops.
                    frames, position = await self.read_batch(position)
                    for frame in frames:
                        yield frame
                    return

                waiter.clear()
                try:
                    await asyncio.wait_for(
                        waiter.wait(), timeout=self.poll_interval_seconds
                    )
                except TimeoutError:
                    idle_seconds += self.poll_interval_seconds
                    if (
                        max_idle_seconds is not None
                        and idle_seconds >= max_idle_seconds
                    ):
                        return


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "RunEventStreamReader",
    "RunNotVisibleError",
    "RunStreamAuthorizationError",
    "RunStreamEntitlement",
    "RunStreamHub",
    "StreamFrame",
    "run_stream_hub",
]
