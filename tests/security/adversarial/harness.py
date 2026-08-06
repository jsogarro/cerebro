"""What it takes to run the corpus against the real boundary, and say so honestly.

The corpus was authored against no implementation. This module supplies the
apparatus that lets each scenario be *executed* — a real
:class:`~src.core.tools.ToolBoundary` with real specifications, real grants, and
a real clock — plus the vocabulary for reporting what happened.

**Four verdicts, because "pass" and "fail" cannot carry the distinction that
matters.** The corpus states two things for every scenario: the mechanism it
expects (``expected_outcome.statement``) and the floor that must hold when that
mechanism is absent (``expected_outcome.on_missing_enforcement``, which is
always denial). Collapsing those into one boolean loses precisely the
information a reader needs, so :class:`Verdict` keeps them apart:

``HELD``
    The named mechanism exists and enforced the guarantee.
``HELD_VIA_FLOOR``
    The named mechanism does **not** exist, but the request still failed
    closed. Real, and weaker than it reads — every one names what is missing.
``VIOLATED``
    The floor itself failed: the call fell through to allow, or the effect
    occurred. Per the corpus's own consumption contract this is a CRITICAL
    finding regardless of a scenario's ``strength``.
``NOT_EXERCISABLE``
    There is nothing to run it against. Recorded with the reason, never
    silently converted into a pass.

**Every non-violated observation carries a control, and the control carries an
outcome rather than a sentence.** A scenario asserting "the capability check
denies" proves nothing if the same boundary denies everything — a control that
cannot succeed is indistinguishable from one that correctly refuses. So
:class:`Observation` requires :class:`Control`: a paired, same-boundary,
same-grant call that was *allowed*, or the equivalent demonstration that the
mechanism under test discriminated.

**Why this is a type and not a string.** The first version of this file
required ``control`` to be a non-empty ``str``, and every exerciser duly
computed a real control call and then interpolated its status into prose. That
requirement checks that a *sentence exists*. Mutating ``_identity_matches`` to
``return False and (...)`` — a boundary that denies literally everything — left
18 of the corpus's scenarios still reporting their expected verdict, several of
them printing ``the same boundary ... allowed academic_search (status=denied)``.
The sentence and the fact disagreed, silently, in the mechanism built to stop
exactly that.

:class:`Control` therefore stores *the outcome it observed* — a status, a
handler-invocation count, a pair of recorded results — and derives its English
from those fields via :meth:`Control.sentence`. "The control went the other way"
becomes :attr:`Control.demonstrated`, which
:meth:`Observation.__post_init__` refuses to accept as false. There is no way to
satisfy it by writing a better string, and when a sibling change moves an
outcome the refusal names the observed value rather than quietly re-wording
itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, final

from pydantic import BaseModel, Field

from src.agents.tools.mediation import InMemoryToolAuditStore
from src.core.contracts.capabilities import (
    ApprovalRef,
    CapabilityGrant,
    SensitivityClass,
)
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    MappingSecretProvider,
    NullEventPublisher,
    ToolBoundary,
    ToolCallContext,
    ToolOutcome,
    ToolOutcomeStatus,
    ToolSpec,
)

__all__ = [
    "ORG_A",
    "ORG_B",
    "SENTINEL_SECRET",
    "Boundary",
    "Control",
    "ControlKind",
    "MutableClock",
    "Observation",
    "Verdict",
    "approval_for",
    "build_boundary",
    "grant_for",
]


class Verdict(StrEnum):
    """What running one scenario against the real system established."""

    HELD = "held"
    HELD_VIA_FLOOR = "held_via_floor"
    VIOLATED = "violated"
    NOT_EXERCISABLE = "not_exercisable"


class ControlKind(StrEnum):
    """How a scenario showed its mechanism could have gone the other way.

    ``ALLOWED_CALL``
        A second, real call on the *same* boundary and the *same* grant list
        that the boundary **allowed**. This is the strongest form and the only
        one a capability-mediated guarantee may use: it cannot be constructed
        without a :class:`~src.core.tools.ToolOutcome`, and a boundary that
        refuses everything cannot produce one.
    ``CONTRASTING_RESULT``
        The mechanism under test is not the capability layer — a sanitizer, the
        redaction contract, a model validator, the idempotency ledger — so the
        demonstration is that the same mechanism recorded *different* results
        for the attack input and a benign one. Satisfied only when the two
        recorded values actually differ.
    ``NO_CAPABILITY_CONTROL``
        Declared absence. There is no mechanism whose opposite outcome could be
        reached, so nothing is claimed. Never counts as a demonstration; the
        scenarios allowed to use it are pinned by id in the suite, so the set of
        passes standing on nothing stays visible and cannot grow quietly.
    """

    ALLOWED_CALL = "allowed_call"
    CONTRASTING_RESULT = "contrasting_result"
    NO_CAPABILITY_CONTROL = "no_capability_control"


def _render(value: object) -> str:
    """Render a recorded value for a control, stably and readably."""

    if isinstance(value, StrEnum):
        return value.value
    return repr(value)


@final
@dataclass(frozen=True, slots=True)
class Control:
    """The outcome a scenario observed on the other side of its mechanism.

    Constructed only through the three factories below, each of which reads its
    fields off something that actually ran. :attr:`demonstrated` is computed
    from those fields; :meth:`sentence` is *derived* from them. Nothing here can
    be satisfied by asserting a nicer string.
    """

    kind: ControlKind
    what: str
    """What the paired observation was, in the scenario's own terms."""

    observed: str = ""
    """The control side's recorded result."""

    counterpart: str = ""
    """The attack side's recorded result — ``CONTRASTING_RESULT`` only."""

    handler_invocations: int | None = None
    """How many times the allowed call's handler ran, when the scenario knows."""

    why_absent: str = ""
    """Why no control exists — ``NO_CAPABILITY_CONTROL`` only."""

    def __post_init__(self) -> None:
        if not self.what.strip():
            raise ValueError("a control names what was paired against")
        if (
            self.kind is ControlKind.NO_CAPABILITY_CONTROL
            and not self.why_absent.strip()
        ):
            raise ValueError(
                "a declared-absent control states why no opposite outcome is "
                "reachable; 'there was no time' is not a reason"
            )

    @classmethod
    def allowed_call(
        cls,
        outcome: ToolOutcome,
        *,
        what: str,
        handler_invocations: int | None = None,
    ) -> Control:
        """Record a paired call the boundary allowed.

        Takes the :class:`ToolOutcome` rather than its status, so the control
        cannot be written without the call having been made.
        """

        return cls(
            kind=ControlKind.ALLOWED_CALL,
            what=what,
            observed=outcome.status.value,
            handler_invocations=handler_invocations,
        )

    @classmethod
    def contrasting_result(
        cls, *, what: str, on_attack: object, on_control: object
    ) -> Control:
        """Record the same mechanism producing two different results."""

        return cls(
            kind=ControlKind.CONTRASTING_RESULT,
            what=what,
            observed=_render(on_control),
            counterpart=_render(on_attack),
        )

    @classmethod
    def absent(cls, *, what: str, why: str) -> Control:
        """Declare that no opposite outcome is reachable, and say why."""

        return cls(kind=ControlKind.NO_CAPABILITY_CONTROL, what=what, why_absent=why)

    @property
    def demonstrated(self) -> bool:
        """Whether the mechanism was observed producing the opposite outcome."""

        if self.kind is ControlKind.ALLOWED_CALL:
            return self.observed == ToolOutcomeStatus.SUCCEEDED.value and (
                self.handler_invocations is None or self.handler_invocations > 0
            )
        if self.kind is ControlKind.CONTRASTING_RESULT:
            return bool(self.observed) and self.observed != self.counterpart
        return False

    @property
    def diagnosis(self) -> str:
        """Why :attr:`demonstrated` is false, in terms a reader can act on."""

        if self.kind is ControlKind.ALLOWED_CALL:
            if self.observed != ToolOutcomeStatus.SUCCEEDED.value:
                return (
                    f"the paired call was expected to be ALLOWED and came back "
                    f"status={self.observed}. Either the mechanism under test "
                    f"now refuses this call too — in which case the verdict "
                    f"above is a denial on a boundary that denies everything — "
                    f"or the control itself needs rewiring."
                )
            return (
                f"the paired call succeeded but its handler ran "
                f"{self.handler_invocations} time(s), so nothing was actually "
                f"reached"
            )
        if self.kind is ControlKind.CONTRASTING_RESULT:
            return (
                f"the mechanism recorded the same result for the attack input "
                f"and the benign one ({self.counterpart!r} vs {self.observed!r}), "
                f"so it is not discriminating — it is answering unconditionally"
            )
        return self.why_absent

    def sentence(self) -> str:
        """The human-readable control, derived from what was recorded."""

        if self.kind is ControlKind.ALLOWED_CALL:
            reached = (
                ""
                if self.handler_invocations is None
                else f", handler_invocations={self.handler_invocations}"
            )
            return (
                f"{self.what} — the boundary ALLOWED it "
                f"(status={self.observed}{reached}), so the outcome above is a "
                f"decision rather than a boundary that refuses everything"
            )
        if self.kind is ControlKind.CONTRASTING_RESULT:
            return (
                f"{self.what} — the same mechanism recorded "
                f"{self.counterpart} for the attack input and {self.observed} "
                f"for the benign one, so it discriminates rather than answering "
                f"unconditionally"
            )
        return f"{self.what} — no control exists: {self.why_absent}"


@final
@dataclass(frozen=True, slots=True)
class Observation:
    """One scenario's true outcome, with the evidence for it."""

    verdict: Verdict
    evidence: str
    control: Control | None = None
    """The opposite outcome the mechanism under test was observed producing.

    Required for :attr:`Verdict.HELD` and :attr:`Verdict.HELD_VIA_FLOOR`, and
    required to be :attr:`Control.demonstrated` unless it explicitly declares
    itself absent. A denial observed on a boundary that denies everything is not
    evidence of a working check, and neither is a sentence saying it was.
    """

    weakened_by: tuple[str, ...] = ()
    """Why this pass is worth less than it reads — one entry per reason.

    Unlike :attr:`control` this is irreducibly prose: "the grant is self-issued"
    names no outcome a machine can compare. What *is* checkable is that each
    entry says something, and that is enforced below — the suite's ADVISORY
    guard tests ``assert observation.weakened_by``, which is a tuple-truthiness
    check that ``("",)`` satisfies. That is the same "a sentence exists" shape
    this module was rewritten to remove from :attr:`control`, so the blank case
    is closed here rather than left as the one place it still worked.

    This does not make a caveat load-bearing the way a control is. It closes the
    floor, and the distinction is worth keeping straight.
    """

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            raise ValueError("an observation states its evidence")
        for index, reason in enumerate(self.weakened_by):
            if not reason.strip():
                raise ValueError(
                    f"weakened_by[{index}] is blank. An observation that says it "
                    "is worth less than it reads has to say why; an empty "
                    "string satisfies the suite's ADVISORY guard while stating "
                    "nothing."
                )
        if self.verdict not in _CONTROL_REQUIRED:
            return
        if self.control is None:
            raise ValueError(
                f"a {self.verdict.value} observation must carry the control that "
                "shows the mechanism could have produced the opposite outcome. "
                "IF THIS SCENARIO IS RECORDED IN `DEFECTS`, THE LIKELY CAUSE IS "
                "THAT ITS DEFECT HAS BEEN FIXED: a violated scenario carries no "
                "control because there is no pass to justify, so the first "
                "run in which the guarantee holds arrives here. Write the "
                "control, delete the entry from DEFECTS, and drop the xfail."
            )
        if (
            not self.control.demonstrated
            and self.control.kind is not ControlKind.NO_CAPABILITY_CONTROL
        ):
            raise ValueError(
                f"a {self.verdict.value} observation was reported, but its "
                f"control did not go the other way: {self.control.diagnosis} "
                f"(control: {self.control.what})"
            )

    @property
    def control_sentence(self) -> str:
        """The control as prose, or an explicit note that there is none."""

        return "" if self.control is None else self.control.sentence()


_CONTROL_REQUIRED: Final[frozenset[Verdict]] = frozenset(
    {Verdict.HELD, Verdict.HELD_VIA_FLOOR}
)


# ----------------------------------------------------------------------------
# fixed identifiers
# ----------------------------------------------------------------------------

ORG_A: Final[str] = "11111111-1111-1111-1111-111111111111"
ORG_B: Final[str] = "22222222-2222-2222-2222-222222222222"

SENTINEL_SECRET: Final[str] = "sk-live-CEREBRO-TEST-SENTINEL-9f8e7d6c5b4a3210"
"""The literal value the secret-leakage class asserts on.

Matched exactly rather than by shape: a credential-*looking* string appearing in
a record proves nothing, while this exact configured value appearing proves a
leak. Kept identical to the corpus's own sentinel so the two cannot drift.
"""

T0: Final[datetime] = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)


@final
class MutableClock:
    """A clock the test advances, so time-of-use gaps are reachable.

    The boundary reads its clock once per call by construction. Exercising a
    time-of-check/time-of-use scenario therefore requires moving time *while a
    handler is running*, which a frozen instant cannot express and the wall
    clock cannot do reproducibly.
    """

    __slots__ = ("_now",)

    def __init__(self, start: datetime = T0) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    @property
    def now(self) -> datetime:
        return self._now


@final
@dataclass(slots=True)
class Boundary:
    """A wired boundary plus the collaborators a scenario needs to inspect."""

    boundary: ToolBoundary
    store: InMemoryToolAuditStore
    publisher: NullEventPublisher
    clock: MutableClock
    calls: dict[str, list[Mapping[str, Any]]] = field(default_factory=dict)
    """Every argument set each tool handler actually received, by tool name.

    This is how "the tool was never reached" is established as a fact rather
    than inferred from a status code.
    """

    def call_count(self, tool_name: str) -> int:
        return len(self.calls.get(tool_name, ()))


# ----------------------------------------------------------------------------
# tool specifications
# ----------------------------------------------------------------------------


class SearchInput(BaseModel):
    """A search tool's input, with no declared bound on any field.

    Deliberately unbounded, because that is what the shipped tool inputs look
    like: nothing in ``ToolSpec`` registration requires a size or range bound
    the way it requires a timeout and a sensitivity. The oversized-input class
    tests exactly that gap, so declaring a bound here would test this file
    instead of the boundary.
    """

    query: str
    max_results: int = 10


class SearchOutput(BaseModel):
    title: str
    citation_count: int


class NotificationInput(BaseModel):
    recipient: str
    body: str


class NotificationOutput(BaseModel):
    delivered: bool


class ProjectInput(BaseModel):
    project_id: str


class ProjectOutput(BaseModel):
    deleted: bool


class FetchInput(BaseModel):
    url: str


class FetchOutput(BaseModel):
    body: str


class ExportInput(BaseModel):
    target: str


class ExportOutput(BaseModel):
    exported: bool


class AppendInput(BaseModel):
    chunk: str


class AppendOutput(BaseModel):
    appended: bool


class QueryInput(BaseModel):
    statement: str


class QueryOutput(BaseModel):
    rows: int


class NestedInput(BaseModel):
    """An input with a free-form object field.

    A tool may declare one — ``JsonObject`` is a contract primitive and nothing
    at registration forbids it — so the nesting-depth scenario has a legitimate
    shape to arrive through.
    """

    filter: dict[str, Any] = Field(default_factory=dict)


class NestedOutput(BaseModel):
    accepted: bool


ACADEMIC_SEARCH: Final[str] = "academic_search"
SEND_NOTIFICATION: Final[str] = "send_notification"
DELETE_PROJECT: Final[str] = "delete_project"
CITATION_FETCH: Final[str] = "citation_fetch"
EXPORT_BUNDLE: Final[str] = "export_evidence_bundle"
APPEND_SHARED_DOC: Final[str] = "append_to_shared_doc"
DATABASE_QUERY: Final[str] = "database_query"
NESTED_FILTER: Final[str] = "nested_filter"


def _recorder(
    wired: Boundary, tool_name: str, result: Callable[[Any], Mapping[str, Any]]
) -> Any:
    """Build a handler that records what it was given, then answers."""

    async def handler(payload: Any, _context: ToolCallContext) -> Mapping[str, Any]:
        wired.calls.setdefault(tool_name, []).append(payload.model_dump())
        return result(payload)

    return handler


def _register_default_tools(wired: Boundary) -> None:
    """Register the tools the corpus's entry paths name.

    Sensitivities are assigned by what the call can do outside its run, which
    is what the capability layer authorizes against: sending a run's content to
    an external recipient is ``EXFILTRATION``, deleting a project is an
    ``INTERNAL_WRITE``, and reading a source is ``READ_ONLY``.
    """

    specs = (
        ToolSpec(
            name=ACADEMIC_SEARCH,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=SearchInput,
            output_model=SearchOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired,
                ACADEMIC_SEARCH,
                lambda _payload: {"title": "Deep Learning", "citation_count": 5},
            ),
        ),
        ToolSpec(
            name=SEND_NOTIFICATION,
            version="1.0.0",
            sensitivity=SensitivityClass.EXFILTRATION,
            input_model=NotificationInput,
            output_model=NotificationOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired, SEND_NOTIFICATION, lambda _payload: {"delivered": True}
            ),
        ),
        ToolSpec(
            name=DELETE_PROJECT,
            version="1.0.0",
            sensitivity=SensitivityClass.INTERNAL_WRITE,
            input_model=ProjectInput,
            output_model=ProjectOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired, DELETE_PROJECT, lambda _payload: {"deleted": True}
            ),
        ),
        ToolSpec(
            name=CITATION_FETCH,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=FetchInput,
            output_model=FetchOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired,
                CITATION_FETCH,
                lambda payload: {"body": f"fetched {payload.url}"},
            ),
        ),
        ToolSpec(
            name=EXPORT_BUNDLE,
            version="1.0.0",
            sensitivity=SensitivityClass.EXFILTRATION,
            input_model=ExportInput,
            output_model=ExportOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired, EXPORT_BUNDLE, lambda _payload: {"exported": True}
            ),
        ),
        ToolSpec(
            name=APPEND_SHARED_DOC,
            version="1.0.0",
            sensitivity=SensitivityClass.INTERNAL_WRITE,
            input_model=AppendInput,
            output_model=AppendOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired, APPEND_SHARED_DOC, lambda _payload: {"appended": True}
            ),
        ),
        ToolSpec(
            name=DATABASE_QUERY,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=QueryInput,
            output_model=QueryOutput,
            timeout_seconds=5.0,
            handler=_recorder(wired, DATABASE_QUERY, lambda _payload: {"rows": 0}),
        ),
        ToolSpec(
            name=NESTED_FILTER,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=NestedInput,
            output_model=NestedOutput,
            timeout_seconds=5.0,
            handler=_recorder(
                wired, NESTED_FILTER, lambda _payload: {"accepted": True}
            ),
        ),
    )
    for spec in specs:
        wired.boundary.register(spec)


def build_boundary(
    *,
    secrets: Mapping[str, str] | None = None,
    extra_specs: Sequence[ToolSpec] = (),
    clock: MutableClock | None = None,
) -> Boundary:
    """Wire a boundary with the corpus's tools and a held sentinel secret.

    ``secrets`` defaults to holding :data:`SENTINEL_SECRET`, so redaction's
    exact-value layer is live rather than silently absent — the omission packet
    4A named as invisible at a call site.
    """

    the_clock = clock if clock is not None else MutableClock()
    store = InMemoryToolAuditStore()
    publisher = NullEventPublisher()
    boundary = ToolBoundary(
        secret_provider=MappingSecretProvider(
            dict(secrets) if secrets is not None else {"provider-key": SENTINEL_SECRET}
        ),
        audit_store=store,
        event_publisher=publisher,
        clock=the_clock,
    )
    wired = Boundary(
        boundary=boundary, store=store, publisher=publisher, clock=the_clock
    )
    _register_default_tools(wired)
    for spec in extra_specs:
        boundary.register(spec)
    return wired


# ----------------------------------------------------------------------------
# grants and approvals
# ----------------------------------------------------------------------------

RUN_A: Final[str] = "run-tenant-a-001"
TASK_A: Final[str] = "task-literature-review"
ATTEMPT_A: Final[str] = "attempt-1"


def grant_for(
    *,
    tool_name: str,
    sensitivity: SensitivityClass,
    capability_scope: str,
    run_id: str = RUN_A,
    task_id: str = TASK_A,
    grant_id: str | None = None,
    max_input_trust: TrustClassification = TrustClassification.EXTERNAL_UNTRUSTED,
    tool_versions: tuple[str, ...] = ("1.0.0",),
    issued_at: datetime = T0,
    ttl: timedelta = timedelta(minutes=10),
) -> CapabilityGrant:
    """Issue one task-scoped grant.

    Every constraint is a *declared* value rather than one read off a request,
    for the reason packet 4A demonstrated: a ceiling minted from the call
    permits all five trust labels and produces a check that cannot fail.
    """

    return CapabilityGrant(
        grant_id=grant_id or f"grant-{tool_name}-{capability_scope}",
        run_id=run_id,
        task_id=task_id,
        capability_scope=capability_scope,
        tool_name=tool_name,
        tool_versions=tool_versions,
        sensitivity=sensitivity,
        max_input_trust=max_input_trust,
        requires_approval=sensitivity
        in {SensitivityClass.EXTERNAL_WRITE, SensitivityClass.EXFILTRATION},
        issued_at=issued_at,
        expires_at=issued_at + ttl,
    )


def approval_for(
    *,
    grant: CapabilityGrant,
    request_fingerprint: str,
    approval_id: str = "approval-1",
    approved_at: datetime = T0,
    ttl: timedelta = timedelta(minutes=10),
) -> ApprovalRef:
    """Bind one approval to one exact request fingerprint."""

    return ApprovalRef(
        approval_id=approval_id,
        grant_id=grant.grant_id,
        request_fingerprint=request_fingerprint,
        approved_by="operator-1",
        approved_at=approved_at,
        expires_at=approved_at + ttl,
    )


async def slow_handler_spec(
    *,
    name: str,
    seconds: float,
    timeout_seconds: float,
    sensitivity: SensitivityClass = SensitivityClass.READ_ONLY,
) -> ToolSpec:
    """Build a tool whose handler outlasts a chosen deadline.

    Used by the two time-of-use scenarios and the amplification scenario, all
    three of which need a call that is still running when something else
    changes.
    """

    async def handler(_payload: Any, _context: ToolCallContext) -> Mapping[str, Any]:
        await asyncio.sleep(seconds)
        return {"body": "done"}

    return ToolSpec(
        name=name,
        version="1.0.0",
        sensitivity=sensitivity,
        input_model=FetchInput,
        output_model=FetchOutput,
        timeout_seconds=timeout_seconds,
        handler=handler,
    )
