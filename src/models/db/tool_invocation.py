"""Durable persistence for ``agent_tool_invocations``.

Mirrors ``src.core.contracts.provenance.ToolInvocation``. Rows are mutable
(``BaseModel``): an invocation moves through
``requested`` -> ``running`` -> a terminal status on the same row, matching
``agent_task_attempts``.

``capability_decision_effect``, ``request_fingerprint``, and the nullable
``capability_grant_id`` / ``capability_approval_id`` / ``capability_denial_reason``
columns record the outcome of the capability check that governed this
invocation (``src.core.contracts.capabilities.CapabilityDecision``). The
CHECKs below are read directly off
``CapabilityDecision.validate_effect_fields`` rather than a simplified
biconditional on ``capability_grant_id``: an ``ALLOW`` decision always names
a grant, but a ``DENY`` decision **may also** name one — ``decide_capability``
reports the grant that got furthest through its checks even when it denies,
because "your approval expired" is more actionable than "no matching grant".
A CHECK requiring ``capability_grant_id IS NULL`` on every denial would
therefore reject a real, correctly-formed denial. ``capability_denial_reason``
is the one field that *is* a true biconditional on effect, and
``capability_approval_id`` is one-directional (a denial never cites an
approval; an allow may or may not, since approval is only required for
sensitive sinks).

``capability_decision_effect`` and ``request_fingerprint`` are **nullable
together**, for exactly one status: input validation runs before
authorization and must, so a request whose input never parses (``error_code
= 'invalid_input'``) never reaches ``decide_capability`` and has no
``CapabilityRequest`` to fingerprint. Writing ``'allow'`` for that row would
be fabrication — it would claim a capability decision authorized a call that
was never authorized — and dropping the row reopens the audit hole this
wave exists to close. A ``NULL`` decision on a row whose ``error_code`` says
why is self-describing instead: it states, truthfully, that authorization
was never reached. ``ck_agent_tool_invocation_decision_pair_or_invalid_input``
pins this narrowly to that one ``error_code`` rather than a blanket "decision
may be absent" escape hatch — a row cannot omit the decision by any other
route.

The full decision is not otherwise duplicated here; the tool-execution
boundary (Wave 4 packet 4C) is expected to publish it through the run event
stream via ``RunEventRepository.append_event``, which is the one event
mechanism this persistence layer supports.

``input_sha256``/``output_sha256`` are ``boundary_digest`` over the
**redacted** ``input``/``output`` JSON — computed by
``ToolInvocationRepository``, not supplied by the ``ToolInvocation`` contract,
which carries no digest fields of its own. ``request_fingerprint`` is copied
from the governing ``CapabilityDecision.request_fingerprint``.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import ToolInvocationStatus, TrustClassification
from src.models.db.base import UUID, BaseModel
from src.models.db.provenance_columns import (
    OptionalPromptBindingMixin,
    nullable_status_check,
    producer_kind_biconditional_check,
    prompt_binding_all_or_nothing_check,
)
from src.models.db.run_lifecycle import CONTRACT_SCHEMA_VERSION, status_check

CAPABILITY_DECISION_EFFECTS_SQL = "('allow', 'deny')"


class AgentToolInvocation(BaseModel, OptionalPromptBindingMixin):
    """An auditable request to one versioned tool capability."""

    __tablename__ = "agent_tool_invocations"

    tool_invocation_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    run_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_runs.run_id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_run_tasks.task_id"), nullable=False, index=True
    )
    attempt_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_task_attempts.attempt_id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    capability_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)

    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_trust: Mapped[str] = mapped_column(String(30), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_trust: Mapped[str | None] = mapped_column(String(30), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    capability_decision_effect: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    capability_grant_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_capability_grants.grant_id"),
        nullable=True,
    )
    capability_approval_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_capability_approvals.approval_id"),
        nullable=True,
    )
    capability_denial_reason: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "idempotency_key",
            name="uq_agent_tool_invocation_idempotency",
        ),
        status_check(
            column="status",
            statuses=ToolInvocationStatus,
            name="ck_agent_tool_invocation_status",
        ),
        status_check(
            column="input_trust",
            statuses=TrustClassification,
            name="ck_agent_tool_invocation_input_trust",
        ),
        nullable_status_check(
            column="output_trust",
            statuses=TrustClassification,
            name="ck_agent_tool_invocation_output_trust",
        ),
        prompt_binding_all_or_nothing_check(
            name="ck_agent_tool_invocation_prompt_binding_all_or_nothing"
        ),
        producer_kind_biconditional_check(
            name="ck_agent_tool_invocation_producer_kind_biconditional"
        ),
        CheckConstraint(
            f"capability_decision_effect IS NULL OR "
            f"capability_decision_effect IN {CAPABILITY_DECISION_EFFECTS_SQL}",
            name="ck_agent_tool_invocation_capability_decision_effect",
        ),
        CheckConstraint(
            "capability_decision_effect = 'allow' OR status = 'denied'",
            name="ck_agent_tool_invocation_capability_denial",
        ),
        # A success is the one status that cannot predate authorization.
        # The constraint above abstains here rather than rejecting: with no
        # decision it reads `NULL OR FALSE`, which is NULL, and a CHECK only
        # rejects FALSE -- so the rule that reads as "a success was
        # authorized" says nothing about exactly the rows that skipped
        # authorization. The pairing constraint below confines a
        # decision-less row to error_code = 'invalid_input' but says nothing
        # about its status, so without this a row could claim to have both
        # failed validation and succeeded.
        # Spelled with an explicit IS NOT NULL for the same reason as that
        # constraint, and portably: Postgres wants IS NOT DISTINCT FROM where
        # SQLite wants IS, and this form needs neither.
        CheckConstraint(
            "status <> 'succeeded' OR (capability_decision_effect IS NOT NULL "
            "AND capability_decision_effect = 'allow')",
            name="ck_agent_tool_invocation_success_requires_allow",
        ),
        # An ALLOW decision always names the grant that permitted it.
        CheckConstraint(
            "capability_decision_effect <> 'allow' OR capability_grant_id IS NOT NULL",
            name="ck_agent_tool_invocation_allow_requires_grant",
        ),
        # A DENY decision must say why; an ALLOW decision must not carry a
        # denial reason. Together this is the true biconditional
        # `CapabilityDecision.validate_effect_fields` enforces on
        # `denial_reason` — unlike `grant_id`, which a denial may still name
        # (the furthest-matching grant), `denial_reason` presence is a clean
        # function of `effect` alone.
        CheckConstraint(
            "capability_decision_effect <> 'deny' OR capability_denial_reason IS NOT NULL",
            name="ck_agent_tool_invocation_deny_requires_reason",
        ),
        CheckConstraint(
            "capability_decision_effect = 'deny' OR capability_denial_reason IS NULL",
            name="ck_agent_tool_invocation_allow_forbids_reason",
        ),
        # A DENY decision never cites an approval (the contract forbids it);
        # an ALLOW decision may or may not, since approval is only required
        # for sensitive sinks.
        CheckConstraint(
            "capability_decision_effect = 'allow' OR capability_approval_id IS NULL",
            name="ck_agent_tool_invocation_deny_forbids_approval",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_agent_tool_invocation_completed_after_requested",
        ),
        CheckConstraint(
            "length(input_sha256) = 64", name="ck_agent_tool_invocation_input_digest"
        ),
        CheckConstraint(
            "output_sha256 IS NULL OR length(output_sha256) = 64",
            name="ck_agent_tool_invocation_output_digest",
        ),
        CheckConstraint(
            "request_fingerprint IS NULL OR length(request_fingerprint) = 64",
            name="ck_agent_tool_invocation_request_fingerprint_digest",
        ),
        # A decision may be absent only for the one status that is
        # structural, not an oversight: input validation runs before
        # authorization, so a request whose input never parses has nothing
        # for decide_capability to decide about and no CapabilityRequest to
        # fingerprint. Pinned narrowly to error_code = 'invalid_input' --
        # NOT NULL-checked explicitly on error_code first, because
        # `error_code = 'invalid_input'` alone evaluates to NULL (not FALSE)
        # when error_code IS NULL, which would let a row with no error_code
        # at all also skip the decision undetected.
        CheckConstraint(
            "(capability_decision_effect IS NOT NULL AND request_fingerprint IS NOT NULL) "
            "OR (capability_decision_effect IS NULL AND request_fingerprint IS NULL "
            "AND error_code IS NOT NULL AND error_code = 'invalid_input')",
            name="ck_agent_tool_invocation_decision_pair_or_invalid_input",
        ),
        Index(
            "idx_agent_tool_invocation_run_task_attempt",
            "run_id",
            "task_id",
            "attempt_id",
        ),
        Index(
            "idx_agent_tool_invocation_org_status",
            "organization_id",
            "status",
            "created_at",
        ),
        Index("idx_agent_tool_invocation_run_status", "run_id", "status"),
        Index("idx_agent_tool_invocation_attempt", "attempt_id", "requested_at"),
        # The security-review query: every denial for an org, most recent
        # first.
        Index(
            "idx_agent_tool_invocation_denied",
            "organization_id",
            "capability_decision_effect",
            "requested_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentToolInvocation(tool_invocation_id={self.tool_invocation_id}, "
            f"status={self.status})>"
        )


__all__ = ["AgentToolInvocation"]
