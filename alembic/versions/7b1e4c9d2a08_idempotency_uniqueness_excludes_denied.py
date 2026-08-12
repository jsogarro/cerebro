"""Exclude denied tool invocations from idempotency uniqueness.

An idempotency key reserves one unit of *work*. A refusal is not work — it is
a decision event about a call that never ran — so a denied row must not occupy
the key. ``uq_agent_tool_invocation_idempotency`` was an unconditional
``UNIQUE (attempt_id, idempotency_key)``, which made two proven defects
inevitable the moment a session-backed audit store is wired behind
``ToolBoundary`` (``ToolBoundary._record`` does not swallow persistence
failures, deliberately — the ``REQUESTED`` write precedes execution, so
swallowing it would let a tool run with no durable record):

1. Calling **any** tool twice with no grant collided with itself. The
   boundary's ``_derive_idempotency_key`` covers tool name and version, run,
   task, attempt, capability scope and input digest — never the grants, the
   caller, or the decision — so two identical refusals derive one key, and the
   denial path persists unconditionally because it terminates above the dedup
   lookup. A repeated attempt on a tool the caller cannot reach left one row
   where forensics needs a count.

2. A denied row bricked the key for a later **authorized** caller, who was
   neither served from the record (``DENIED`` is excluded from the boundary's
   replayable statuses, correctly — one caller's refusal must never become
   another's answer) nor able to write its own. The ``IntegrityError``
   surfaced at the victim.

Namespacing the key by decision effect was considered and does not work: the
derivation reads no grant and no caller, so two identical ungranted calls
derive the identical key and would simply collide inside the ``denied``
namespace instead.

A partial index is the only way to express this — a table constraint carries
no predicate — so the constraint is dropped and a unique index of the same
name replaces it, plus a plain non-unique index on the same pair. The plain
index is not redundant: the replay lookup must see denied rows, which the
partial unique index does not cover, and the dropped constraint had been
providing that backing index for free.

**No backfill, no dedup pass, no CONCURRENTLY.** Nothing in production writes
``agent_tool_invocations`` yet — no tool path holds an ``AsyncSession``, and
the only adapters that reach the repository live in tests — so the table is
empty everywhere this runs and the rewrite is data-free. If that ever stops
being true, note that ``downgrade`` restores an unconditional constraint and
will therefore fail on any duplicate the partial index legitimately admitted;
that failure is correct, since collapsing N recorded refusals into one is data
loss and cannot be done silently.

Revision ID: 7b1e4c9d2a08
Revises: 5d04cec6c232
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b1e4c9d2a08"
down_revision: str | Sequence[str] | None = "5d04cec6c232"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_tool_invocations"
UNIQUE_INDEX = "uq_agent_tool_invocation_idempotency"
LOOKUP_INDEX = "ix_agent_tool_invocation_idempotency_lookup"
COLUMNS = ["attempt_id", "idempotency_key"]
NOT_DENIED = "status <> 'denied'"


def upgrade() -> None:
    """Replace the unconditional constraint with a partial unique index."""
    op.drop_constraint(UNIQUE_INDEX, TABLE, type_="unique")
    op.create_index(
        UNIQUE_INDEX,
        TABLE,
        COLUMNS,
        unique=True,
        postgresql_where=sa.text(NOT_DENIED),
        sqlite_where=sa.text(NOT_DENIED),
    )
    op.create_index(LOOKUP_INDEX, TABLE, COLUMNS)


def downgrade() -> None:
    """Restore the unconditional constraint.

    Fails on any ``(attempt_id, idempotency_key)`` pair the partial index
    admitted more than once — repeated refusals. That is the honest outcome:
    the constraint being restored genuinely cannot hold over that data, and
    deleting rows to make it fit would destroy the audit record this table
    exists to keep.
    """
    op.drop_index(LOOKUP_INDEX, table_name=TABLE)
    op.drop_index(UNIQUE_INDEX, table_name=TABLE)
    op.create_unique_constraint(UNIQUE_INDEX, TABLE, COLUMNS)
