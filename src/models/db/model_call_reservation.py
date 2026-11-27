"""Durable call journal: one row per external model call a governed owner makes.

A row is committed *before* the call is admitted and again, as ``may_have_sent``,
before the request leaves the process, so a crash at any point leaves a record
that says what could have been spent. It stores identifiers, counts and a price
digest only: never a prompt or a response.

Settlement is one compare-and-set out of ``open``; every other state is
terminal. ``settled_unknown`` keeps the reserved amounts as a separately
labelled uncertain bound, never as zero and never as provider-billed cost.

Every CHECK is written so it cannot evaluate to NULL (a CHECK rejects only
FALSE): the columns they read are NOT NULL, and nullable columns are tested
with ``IS [NOT] NULL``.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.db.base import UUID, Base

OWNER_KINDS = ("research_run", "evaluation_job", "document_job")
DISPATCH_STATES = ("reserved", "may_have_sent")
SETTLEMENT_STATES = ("open", "settled_known", "settled_unknown", "released_unsent")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ModelCallReservation(Base):
    __tablename__ = "model_call_reservations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    dispatch_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved"
    )
    settlement_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )
    actual_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "call_id", name="uq_model_call_reservations_call"
        ),
        CheckConstraint(
            _in("owner_kind", OWNER_KINDS), name="ck_model_call_reservations_owner_kind"
        ),
        CheckConstraint(
            _in("dispatch_state", DISPATCH_STATES),
            name="ck_model_call_reservations_dispatch_state",
        ),
        CheckConstraint(
            _in("settlement_state", SETTLEMENT_STATES),
            name="ck_model_call_reservations_settlement_state",
        ),
        CheckConstraint(
            "reserved_tokens >= 0 AND reserved_cost_usd >= 0",
            name="ck_model_call_reservations_reserved_non_negative",
        ),
        # Known usage is recorded together or not at all.
        CheckConstraint(
            "(settlement_state = 'settled_known' AND actual_tokens IS NOT NULL "
            "AND actual_cost_usd IS NOT NULL) OR (settlement_state <> 'settled_known' "
            "AND actual_tokens IS NULL AND actual_cost_usd IS NULL)",
            name="ck_model_call_reservations_known_usage",
        ),
        CheckConstraint(
            "(actual_tokens IS NULL OR actual_tokens >= 0) "
            "AND (actual_cost_usd IS NULL OR actual_cost_usd >= 0)",
            name="ck_model_call_reservations_actual_non_negative",
        ),
        # Only a call that provably never left can be released as unbilled.
        CheckConstraint(
            "settlement_state <> 'released_unsent' OR dispatch_state = 'reserved'",
            name="ck_model_call_reservations_unsent_release",
        ),
        CheckConstraint(
            "(settlement_state = 'open' AND settled_at IS NULL) "
            "OR (settlement_state <> 'open' AND settled_at IS NOT NULL)",
            name="ck_model_call_reservations_settled_at",
        ),
        Index(
            "ix_model_call_reservations_owner",
            "organization_id",
            "owner_kind",
            "owner_id",
        ),
    )
