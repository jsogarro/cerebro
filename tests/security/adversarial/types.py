"""Typed vocabulary for the Wave 4 adversarial corpus.

This module defines the shape every attack scenario must take. It has no
dependency on the tool-execution boundary (``src/core/tools/`` does not exist
yet — packet 4C builds it) and no dependency on a harness. It is pure data
plus the structural rules a self-test can check mechanically.

Field-by-field rationale, because a data-only file reads as boilerplate
unless the reasoning is visible:

- ``payload`` is a zero-argument callable rather than a materialized value.
  Most attack strings are small and the callable just returns a literal, but
  the oversized-input class needs to construct megabyte-scale or
  deeply-nested content, and forcing every scenario through the same
  "builder" shape keeps the corpus uniform instead of special-casing one
  attack class's field type.
- ``expected_outcome.guarantee`` is a closed enum (``GuaranteeKind``), not a
  free-text string a harness would have to pattern-match. The wave's
  non-goal is explicit that string matching is not a security boundary, and
  that applies to how this corpus is consumed, not only to what it tests.
- ``expected_outcome.on_missing_enforcement`` is mandatory and always states
  denial. Deny-by-default is the property under test: for every scenario,
  the outcome when the named mechanism is absent or does not match must be
  denial, never silent fall-through to allow.
- ``strength`` tells a reader what a PASS or FAIL actually proves. See the
  module docstring in ``registry.py`` for how a harness should treat each
  value.
- ``expected_to_fail_today`` / ``failure_reason`` record, at authoring time,
  which scenarios the current (boundary-less) system cannot pass. This is a
  snapshot, not a permanent exemption — see ``registry.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

PayloadKind = str | bytes | dict[str, object] | list[object]
PayloadBuilder = Callable[[], PayloadKind]


class AttackClass(StrEnum):
    """The nine attack classes the wave must defend against."""

    INDIRECT_PROMPT_INJECTION = "indirect_prompt_injection"
    POISONED_TOOL_OUTPUT = "poisoned_tool_output"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    CONFUSED_DEPUTY = "confused_deputy"
    CROSS_TENANT_ACCESS = "cross_tenant_access"
    SECRET_LEAKAGE = "secret_leakage"
    REPLAY = "replay"
    OVERSIZED_INPUT = "oversized_input"
    SENSITIVE_ACTIONS = "sensitive_actions"


class EntryChannel(StrEnum):
    """Where the payload enters the system."""

    RETRIEVED_DOCUMENT = "retrieved_document"
    TOOL_OUTPUT = "tool_output"
    TOOL_INPUT = "tool_input"
    USER_QUERY = "user_query"
    EVENT_LOG = "event_log"
    WS_SSE_STREAM = "ws_sse_stream"
    INTER_AGENT_HANDOFF = "inter_agent_handoff"
    HTTP_REQUEST = "http_request"


class EnforcementStrength(StrEnum):
    """What a result on this scenario is actually worth as evidence.

    DETERMINISTIC: the boundary's own non-model logic (capability check,
    tenant filter, schema validation, size limit, idempotency dedup,
    approval-record lookup) is the thing under test. A FAIL is a reportable
    defect in the system, independent of any model's behavior.

    ADVISORY: today's only available defense for this specific payload is a
    model-mediated layer (a classifier, a sanitizer, prompt wording). Per the
    wave's non-goal, that layer is a defense-in-depth signal, not an
    authorization boundary. A harness must not treat a PASS here as
    satisfying the wave's security bar.
    """

    DETERMINISTIC = "deterministic"
    ADVISORY = "advisory"


class GuaranteeKind(StrEnum):
    """The system guarantee a scenario probes, as a closed vocabulary.

    A harness switches on this to route to a real assertion once the
    corresponding subsystem exists; it is never matched against response
    text.
    """

    CAPABILITY_DENIED = "capability_denied"
    TRUST_LABEL_NOT_ESCALATED = "trust_label_not_escalated"
    TOOL_OUTPUT_SCHEMA_REJECTED = "tool_output_schema_rejected"
    CLAIM_REQUIRES_EVIDENCE = "claim_requires_evidence"
    RETRY_BUDGET_ENFORCED = "retry_budget_enforced"
    CONTEXT_BINDING_ENFORCED = "context_binding_enforced"
    TENANT_ISOLATION_ENFORCED = "tenant_isolation_enforced"
    SECRET_ABSENT_FROM_PERSISTED_RECORD = "secret_absent_from_persisted_record"
    IDEMPOTENT_NO_DUPLICATE_EFFECT = "idempotent_no_duplicate_effect"
    SIZE_LIMIT_ENFORCED = "size_limit_enforced"
    APPROVAL_REQUIRED_AND_SCOPED = "approval_required_and_scoped"
    DESTINATION_ALLOWLIST_ENFORCED = "destination_allowlist_enforced"
    NO_DETERMINISTIC_GUARANTEE_TODAY = "no_deterministic_guarantee_today"


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    """A system guarantee, stated as a property — never a string match."""

    guarantee: GuaranteeKind
    statement: str
    on_missing_enforcement: str

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("ExpectedOutcome.statement must not be empty")
        if not self.on_missing_enforcement.strip():
            raise ValueError("ExpectedOutcome.on_missing_enforcement must not be empty")


@dataclass(frozen=True, slots=True)
class Scenario:
    """One adversarial scenario: what enters, expected outcome, and why."""

    scenario_id: str
    attack_class: AttackClass
    title: str
    entry_channel: EntryChannel
    entry_path: str
    payload: PayloadBuilder
    expected_outcome: ExpectedOutcome
    invariant: str
    strength: EnforcementStrength
    notes: str = ""
    expected_to_fail_today: bool = False
    failure_reason: str = ""

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("Scenario.scenario_id must not be empty")
        if not self.title.strip():
            raise ValueError(f"{self.scenario_id}: title must not be empty")
        if not self.entry_path.strip():
            raise ValueError(f"{self.scenario_id}: entry_path must not be empty")
        if not self.invariant.strip():
            raise ValueError(f"{self.scenario_id}: invariant must not be empty")
        if self.strength is EnforcementStrength.ADVISORY and not self.notes.strip():
            raise ValueError(
                f"{self.scenario_id}: ADVISORY scenarios must explain, in "
                "notes, why no deterministic mechanism can be probed yet"
            )
        if self.expected_to_fail_today and not self.failure_reason.strip():
            raise ValueError(
                f"{self.scenario_id}: expected_to_fail_today requires a "
                "non-empty failure_reason"
            )


@dataclass(frozen=True, slots=True)
class ScenarioGroup:
    """A named batch of scenarios for one attack class, built by one module."""

    attack_class: AttackClass
    scenarios: tuple[Scenario, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for scenario in self.scenarios:
            if scenario.attack_class is not self.attack_class:
                raise ValueError(
                    f"{scenario.scenario_id}: attack_class "
                    f"{scenario.attack_class} does not match group "
                    f"{self.attack_class}"
                )
