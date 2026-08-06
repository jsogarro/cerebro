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

**Every non-violated observation carries a control.** A scenario asserting "the
capability check denies" proves nothing if the same boundary denies everything —
a control that cannot succeed is indistinguishable from one that correctly
refuses. So :class:`Observation` requires ``control``: a paired,
same-boundary, same-grant call that was *allowed*, or the equivalent
demonstration that the mechanism under test has a reachable success path. The
suite asserts the field is populated, so an exerciser cannot report a pass
without having shown the other half.
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
    ToolSpec,
)

__all__ = [
    "ORG_A",
    "ORG_B",
    "SENTINEL_SECRET",
    "Boundary",
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


@final
@dataclass(frozen=True, slots=True)
class Observation:
    """One scenario's true outcome, with the evidence for it."""

    verdict: Verdict
    evidence: str
    control: str = ""
    """Proof the mechanism under test had a reachable opposite outcome.

    Required for :attr:`Verdict.HELD` and :attr:`Verdict.HELD_VIA_FLOOR`; the
    suite asserts it. A denial observed on a boundary that denies everything is
    not evidence of a working check.
    """

    weakened_by: tuple[str, ...] = ()
    """Why this pass is worth less than it reads — one entry per reason."""

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            raise ValueError("an observation states its evidence")
        if self.verdict in _CONTROL_REQUIRED and not self.control.strip():
            raise ValueError(
                f"a {self.verdict.value} observation must name the control that "
                "shows the mechanism could have produced the opposite outcome"
            )


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
