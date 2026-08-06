"""Append-only run event stream and its transactional delivery outbox.

``agent_run_events`` is the source of truth for everything observable about a
run. Each event carries a per-run ``sequence`` that increases by one; that
sequence is the stream cursor a client resumes from, and the unique constraint
on ``(run_id, sequence)`` is what makes a gap or a reordering impossible rather
than merely unlikely.

``agent_run_event_outbox`` exists so that recording an event and scheduling its
delivery happen in the same transaction as the state change that produced it.
A relay drains the outbox afterwards. Delivery is **at-least-once**: a consumer
can and will see the same event more than once after a retry, a reconnect, or a
crash between delivery and acknowledgement. Consumers must dedupe on
``idempotency_key``, which is derived from the event's ``deduplication_key`` and
the destination, and is therefore stable across redeliveries.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base

EVENT_ENVELOPE_VERSION = "1.0"


class EventDeliveryStatus(StrEnum):
    """Delivery state of one outbox row for one destination."""

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DELIVERED = "delivered"
    FAILED = "failed"


@register_append_only
class AgentRunEvent(Base):
    """One immutable, ordered fact about a run.

    ``envelope_schema_version`` versions this envelope; ``event_type_version``
    versions the shape of ``payload`` for a given ``event_type``. They move
    independently, and a run pins both so old runs stay readable after a
    contract revision.
    """

    __tablename__ = "agent_run_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.run_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    aggregate_type: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)

    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type_version: Mapped[str] = mapped_column(String(100), nullable=False)
    envelope_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=EVENT_ENVELOPE_VERSION,
        server_default=EVENT_ENVELOPE_VERSION,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the database accepted the event, distinct from occurred_at",
    )

    producer: Mapped[str] = mapped_column(String(255), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(512), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    causation_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        UniqueConstraint(
            "run_id", "deduplication_key", name="uq_agent_run_event_dedup"
        ),
        CheckConstraint("sequence >= 1", name="ck_agent_run_event_sequence"),
        Index(
            "idx_agent_run_event_org_cursor", "organization_id", "run_id", "sequence"
        ),
        Index("idx_agent_run_event_type", "event_type", "occurred_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRunEvent(run_id={self.run_id}, sequence={self.sequence}, "
            f"type={self.event_type})>"
        )


class AgentRunEventOutbox(Base):
    """One pending delivery of one event to one destination.

    Rows are mutable by design — the relay claims, retries, and completes them —
    which is why this table is not append-only. The event it points at is.
    """

    __tablename__ = "agent_run_event_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="Monotonic relay cursor; drains in insertion order",
    )
    event_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_run_events.event_id"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.run_id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    destination: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Stable key consumers dedupe redeliveries on",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="Serialized envelope, so the relay needs no join to deliver",
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EventDeliveryStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Earliest time the relay may claim this row; backoff moves it out",
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "event_id", "destination", name="uq_agent_run_event_outbox_destination"
        ),
        CheckConstraint(
            "status IN ('delivered', 'failed', 'in_flight', 'pending')",
            name="ck_agent_run_event_outbox_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_agent_run_event_outbox_attempts"),
        CheckConstraint(
            "status <> 'delivered' OR delivered_at IS NOT NULL",
            name="ck_agent_run_event_outbox_delivered_at",
        ),
        Index("idx_agent_run_event_outbox_claimable", "status", "available_at", "id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRunEventOutbox(id={self.id}, destination={self.destination}, "
            f"status={self.status})>"
        )
