"""Shared prompt-binding columns and CHECK constraints for the Wave 4 tables.

``ToolInvocation`` and ``Artifact`` both carry an *optional* ``PromptBinding``,
gated by ``ProducerKind`` per
``src.core.contracts.provenance._validate_producer_binding``: a
``model_turn`` record must name the prompt that produced it, a ``system``
record must not, and the four binding fields are set together or not at all.
:class:`OptionalPromptBindingMixin` and the two CHECK-constraint builders
below encode that pair of rules once so both tables enforce it identically.

``producer_kind`` itself carries **no default**, on either table, matching
``agent_evidence``/``agent_claim_supports``: a default lets a caller forget
it and have a model-produced record silently persisted as deterministic —
the exact inversion of what prompt pinning protects. A model judgment
recorded as machine-derived is the silent-wrong path this column exists to
close off, not one it should reopen by omission.

``Evidence`` and ``ClaimSupport`` carry a *required* ``PromptBinding`` — the
evaluator or acquisition step that produced them always has a prompt — so
those models declare their own ``NOT NULL`` columns directly and do not use
this mixin.
"""

from enum import StrEnum

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column


class OptionalPromptBindingMixin:
    """Nullable ``PromptBinding`` columns plus the required, non-defaulted
    ``producer_kind`` that gates them."""

    producer_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    template_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rendered_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


def prompt_binding_all_or_nothing_check(*, name: str) -> CheckConstraint:
    """Return a CHECK requiring all four binding columns set together, or none.

    A row that names a prompt template but not the rendered digest (or any
    other partial combination) does not identify a prompt at all — it is
    either a complete ``PromptBinding`` or the field group is entirely absent.
    """
    return CheckConstraint(
        "(prompt_id IS NULL AND prompt_version IS NULL "
        "AND template_sha256 IS NULL AND rendered_sha256 IS NULL) "
        "OR "
        "(prompt_id IS NOT NULL AND prompt_version IS NOT NULL "
        "AND template_sha256 IS NOT NULL AND rendered_sha256 IS NOT NULL)",
        name=name,
    )


def producer_kind_biconditional_check(*, name: str) -> CheckConstraint:
    """Return a CHECK tying ``producer_kind`` to prompt-binding presence.

    Mirrors ``_validate_producer_binding``: a ``model_turn`` row must carry a
    binding, a ``system`` row must not. Combined with the all-or-nothing
    CHECK above, "no prompt" becomes a typed, database-enforced decision
    rather than a forgotten column.
    """
    return CheckConstraint(
        "(producer_kind = 'system' AND prompt_id IS NULL) "
        "OR (producer_kind = 'model_turn' AND prompt_id IS NOT NULL)",
        name=name,
    )


def nullable_status_check(
    *, column: str, statuses: type[StrEnum], name: str
) -> CheckConstraint:
    """Constrain an optional status-like column to the contract's value set.

    Sibling to ``src.models.db.run_lifecycle.status_check`` for columns that
    are legitimately absent — ``ToolInvocation.output_trust`` has no value
    until the invocation completes — rather than always-required status
    columns.
    """
    allowed = ", ".join(f"'{status.value}'" for status in sorted(statuses))
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({allowed})", name=name)


__all__ = [
    "OptionalPromptBindingMixin",
    "nullable_status_check",
    "producer_kind_biconditional_check",
    "prompt_binding_all_or_nothing_check",
]
