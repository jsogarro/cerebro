"""The aggregated Wave 4 adversarial corpus, and its consumption contract.

## What this module is

``ALL_SCENARIOS`` is the single source of truth for every attack scenario in
this corpus. It aggregates the ``SCENARIOS`` tuple from each module in
``scenarios/`` — one module per attack class. Nothing else should hand-copy
scenario data; import from here.

This module has no dependency on the tool-execution boundary
(``src/core/tools/`` does not exist yet — packet 4C builds it) and no
dependency on any harness. It supplies data and pure filter functions only.

## Consumption contract for a later harness

A harness (built once the tool-execution boundary exists, and by the
independent acceptance reviewer in the meantime) should read this corpus as
follows:

1. **Payloads are builders, not values.** ``Scenario.payload`` is a
   zero-argument callable (``PayloadBuilder``), not a materialized value.
   Call it once per scenario per run to obtain the concrete attack content.
   Most builders return a literal immediately; the oversized-input class
   allocates megabytes of content on call. Do not eagerly materialize every
   scenario's payload up front — iterate and call one at a time.

2. **``strength`` tells you what a result is worth as evidence.**
   - ``EnforcementStrength.DETERMINISTIC``: the boundary's own non-model
     logic is under test. Running this scenario against the real boundary
     and observing the wrong outcome is a reportable defect.
   - ``EnforcementStrength.ADVISORY``: only a model-mediated layer
     (classifier, sanitizer, prompt) can currently be probed for this exact
     payload. Record the model's behavior for visibility, but a PASS here
     does not satisfy the wave's security bar and a FAIL here is not a
     boundary defect, because there is no boundary being tested for this
     specific evasion yet. See each ADVISORY scenario's ``notes`` field for
     why — every one names the deterministic backstop scenario that carries
     the real guarantee, where one exists.

3. **``expected_outcome.guarantee`` is a closed enum, not a string to
   match.** Switch on ``GuaranteeKind`` to route to a real assertion once
   the corresponding subsystem exists (for example,
   ``GuaranteeKind.CAPABILITY_DENIED`` -> assert
   ``invocation.status == ToolInvocationStatus.DENIED``). Per the wave's own
   non-goal, string-matching a response is not how a security boundary is
   verified, and that applies to how this corpus is consumed as much as to
   what it tests.

4. **``expected_outcome.on_missing_enforcement`` is always denial.** For
   every scenario, the stated outcome when the named mechanism is absent or
   does not match is failure/denial — never silent fall-through to allow.
   A harness that observes fall-through to allow has found a CRITICAL
   finding regardless of the scenario's ``strength`` label.

5. **``expected_to_fail_today`` / ``failure_reason`` are a snapshot, not a
   permanent exemption.** They record what this packet's author (with no
   tool-execution boundary yet to test against) already knows the current
   system cannot pass, and why. Re-evaluate every such scenario each time
   the packet its ``failure_reason`` names (4B/4C/4D/4E/4F) lands — a
   harness must not silently xfail these forever.

6. **Filter with the provided pure functions**, not by re-deriving logic
   over ``ALL_SCENARIOS``:
   - ``by_attack_class(AttackClass) -> tuple[Scenario, ...]``
   - ``by_strength(EnforcementStrength) -> tuple[Scenario, ...]``
   - ``expected_to_fail_today_scenarios() -> tuple[Scenario, ...]``

7. **Scenario IDs are stable identifiers**, formatted
   ``<class-slug>-<NN>-<short-description>``. A harness may use
   ``scenario_id`` as a test-parametrization ID or as a key into a
   pass/fail report; do not assume any ordering beyond what
   ``ALL_SCENARIOS`` returns (declaration order, grouped by attack class).
"""

from __future__ import annotations

from .scenarios import (
    confused_deputy,
    cross_tenant_access,
    indirect_prompt_injection,
    oversized_input,
    poisoned_tool_output,
    privilege_escalation,
    replay,
    secret_leakage,
    sensitive_actions,
)
from .types import AttackClass, EnforcementStrength, Scenario

ALL_SCENARIOS: tuple[Scenario, ...] = (
    indirect_prompt_injection.SCENARIOS
    + poisoned_tool_output.SCENARIOS
    + privilege_escalation.SCENARIOS
    + confused_deputy.SCENARIOS
    + cross_tenant_access.SCENARIOS
    + secret_leakage.SCENARIOS
    + replay.SCENARIOS
    + oversized_input.SCENARIOS
    + sensitive_actions.SCENARIOS
)


def by_attack_class(attack_class: AttackClass) -> tuple[Scenario, ...]:
    """Return every scenario for one attack class, in declaration order."""

    return tuple(s for s in ALL_SCENARIOS if s.attack_class is attack_class)


def by_strength(strength: EnforcementStrength) -> tuple[Scenario, ...]:
    """Return every scenario with the given enforcement strength."""

    return tuple(s for s in ALL_SCENARIOS if s.strength is strength)


def expected_to_fail_today_scenarios() -> tuple[Scenario, ...]:
    """Return every scenario the corpus author already expects to fail."""

    return tuple(s for s in ALL_SCENARIOS if s.expected_to_fail_today)


__all__ = [
    "ALL_SCENARIOS",
    "by_attack_class",
    "by_strength",
    "expected_to_fail_today_scenarios",
]
