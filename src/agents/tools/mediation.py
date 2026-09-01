"""What a caller needs to reach the tool boundary, and what it must admit.

Packet 4C built one mediator with capability enforcement, redaction, deadlines,
cancellation, circuit breaking, idempotency, and provenance — and nothing
imported it. This module is the adapter layer that puts the three characterized
paths on it. It holds only what a *caller* needs; the enforcement itself stays
in :mod:`src.core.tools`, which this module does not modify.

Three things here are compromises rather than solutions, and each is named
rather than smoothed over, because a routing layer that quietly invents the
missing pieces is how a boundary comes to mediate a fiction.

**Identity.** The boundary requires a run, task, and attempt id, and 4-Char
found that no public method on any of the three paths threaded one. Callers
that can supply it now do. Callers that cannot get an :meth:`ToolCallIdentity.
unbound` identity whose run id is prefixed ``unbound-`` and whose ``bound``
flag is ``False``, propagated into the returned payload. A call nobody can
correlate is recorded as a call nobody can correlate — not given a plausible
identifier that would make an audit look complete.

**Authorization.** Nothing in this repository issues capability grants. There
is no issuer, no policy source, and no runtime grant writer. The choice was
between denying every call — which would mediate the tool paths by switching
them off — and minting a grant at the call site. This module mints one, from a
static per-tool :class:`SelfIssuedPolicy`, scoped to the exact run and task,
valid for minutes, and carrying a ``self-issued:`` scope prefix.

*A self-issued grant is not authorization.* What it buys is that the check
runs, that mismatched requests — wrong task, wrong run, wrong tool version,
input trust above the tool's declared ceiling — are genuinely refused, and that
the scope written to the record comes off a grant object rather than off the
request. Those are real and testable. What it does not buy is any claim that an
authority permitted the call. When an issuer exists, it injects grants and this
function stops being reached.

**Every field on the grant is a static declaration, never a value read off the
call.** A ceiling minted from the invocation's own ``input_trust`` allows all
five trust levels; a declared one refuses ``derived_untrusted``. The table in
:class:`SelfIssuedPolicy` reproduces both. This is the difference between a
default policy nobody has authorized and a rubber stamp shaped exactly like the
request, and it is the whole reason the check is worth running.

**The blast radius is bounded by the contract, not by discipline here.**
:meth:`~src.core.contracts.capabilities.CapabilityGrant.validate_grant`
*rejects at construction* any grant whose sensitivity is in
:data:`~src.core.contracts.capabilities.APPROVAL_REQUIRED_SENSITIVITIES` with
``requires_approval=False``. So this pattern **cannot** be extended to
``EXTERNAL_WRITE`` or ``EXFILTRATION`` — not by accident, and not by a future
maintainer who copies :func:`self_issued_grant`. Where it stops is exactly
where a real issuer becomes mandatory. The local refusal in that function fails
earlier and names the tool; it is not what makes this true.

**Residual, unenforced:** nothing prevents a future issuer from minting grants
under the reserved prefix. That is a review question, not a check.

**Durability.** :class:`InMemoryToolAuditStore` satisfies 4C's
``ToolAuditStore`` protocol and keeps records for the lifetime of the process.
Wave 3's ``ToolInvocationRepository`` is the durable destination, but reaching
it needs an ``AsyncSession`` factory, and none of the three tool paths has one
— the agents are constructed without database access. The store is injectable
so that wiring is a construction-site change rather than an edit here, and it
is named for what it is so "nothing is durable" is visible at every call site
that accepts the default.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, final

from src.core.contracts.capabilities import (
    APPROVAL_REQUIRED_SENSITIVITIES,
    CapabilityDecision,
    CapabilityGrant,
    SensitivityClass,
)
from src.core.contracts.provenance import ToolInvocation, ToolInvocationStatus
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    NullEventPublisher,
    NullSecretProvider,
    SecretProvider,
    ToolAuditEvent,
    ToolAuditStore,
    ToolBoundary,
    ToolEventPublisher,
)

SELF_ISSUED_SCOPE_PREFIX: Final[str] = "self-issued:"
"""Marks a capability scope that no authority granted.

A naming convention with no enforcement behind it would be exactly the kind of
thing this wave's non-goal rules out as a security boundary. It is not one:
:func:`self_issued_grant` is the only producer, it refuses anything above
``READ_ONLY``, and the prefix exists so an auditor can *find* these calls, not
so anything downstream can trust them.
"""

UNBOUND_RUN_PREFIX: Final[str] = "unbound-"
"""Marks a run identifier that was synthesized because none was supplied."""

RUN_ID_CONTEXT_KEY: Final[str] = "run_id"
"""Context key carrying the durable run identifier for an agent task."""

TASK_ID_CONTEXT_KEY: Final[str] = "task_id"
"""Context key carrying the durable task identifier for an agent task."""

ATTEMPT_ID_CONTEXT_KEY: Final[str] = "attempt_id"
"""Context key carrying the durable attempt identifier for an agent task."""

ORGANIZATION_ID_CONTEXT_KEY: Final[str] = "organization_id"
"""Context key carrying the durable organization identifier for an agent task."""

DEFAULT_GRANT_TTL: Final[timedelta] = timedelta(minutes=5)
"""How long a self-issued grant stays valid.

This must comfortably exceed the boundary's worst-case budget for a single
call, not merely a single attempt. 4C does timeouts, retries and circuit
breaking; if the window closed first, a later attempt would decide against a
newer ``now`` and come back ``GRANT_EXPIRED`` -- which reads as a capability
bug and is really a timeout. The relationship is asserted in
``tests/test_tool_path_mediation.py`` rather than left to this number looking
big enough.
"""

_REPLAYABLE_TERMINAL_STATUSES: Final[frozenset[ToolInvocationStatus]] = frozenset(
    {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
    }
)
_PENDING_STATUSES: Final[frozenset[ToolInvocationStatus]] = frozenset(
    {ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RUNNING}
)


def _organization_key(organization_id: str | None) -> str | None:
    """Normalize the in-memory tenant key without weakening its scope."""

    return None if organization_id is None else str(organization_id)


@final
@dataclass(frozen=True, slots=True)
class ToolCallIdentity:
    """The run, task, and attempt a tool call belongs to."""

    run_id: str
    task_id: str
    attempt_id: str
    organization_id: str | None = None
    bound: bool = True
    """Whether a caller actually supplied this identity.

    ``False`` means it was synthesized. It is carried into the returned payload
    rather than kept internal, because the callers that cannot supply an
    identity are precisely the ones whose records an audit would otherwise
    read as complete.
    """

    @property
    def durable(self) -> bool:
        """Whether this identity has the complete caller-supplied scope.

        ``bound`` only says that a caller supplied an identity-shaped value.
        A durable identity additionally needs every lifecycle identifier and
        its organization boundary; synthesized or partially scoped identities
        must remain distinguishable from one that can resolve to durable rows.
        """

        return self.bound and all(
            (self.run_id, self.task_id, self.attempt_id, self.organization_id)
        )

    @classmethod
    def unbound(cls, *, label: str) -> ToolCallIdentity:
        """Return a marked identity for a caller that has none to give."""

        token = f"{UNBOUND_RUN_PREFIX}{label}-{uuid.uuid4().hex}"
        return cls(
            run_id=token,
            task_id=token,
            attempt_id=token,
            organization_id=None,
            bound=False,
        )

    @classmethod
    def from_agent_task(
        cls, task: Any, *, organization_id: str | None = None
    ) -> ToolCallIdentity:
        """Derive an identity from an ``AgentTask``.

        ``AgentTask`` carries an ``id`` and a free-form ``context``. A run id
        set on the context wins; otherwise the task id stands for all three,
        which is honest — this agent path has no attempt concept, and inventing
        distinct-looking ids for one execution would misrepresent it as three
        correlated records.
        """

        context = getattr(task, "context", None) or {}
        task_id = str(context.get(TASK_ID_CONTEXT_KEY) or getattr(task, "id", "") or "")
        if not task_id:
            return cls.unbound(label="agent-task")
        run_id = str(context.get(RUN_ID_CONTEXT_KEY) or task_id)
        attempt_id = str(context.get(ATTEMPT_ID_CONTEXT_KEY) or task_id)
        return cls(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            organization_id=organization_id
            or (
                str(context[ORGANIZATION_ID_CONTEXT_KEY])
                if context.get(ORGANIZATION_ID_CONTEXT_KEY)
                else None
            ),
        )


@final
@dataclass(frozen=True, slots=True)
class SelfIssuedPolicy:
    """A tool's declared ceilings, written once and never read off a call.

    **Every field here must be a static declaration.** Packet 4A demonstrated
    why against the committed contract: minting ``max_input_trust`` from the
    invocation's own ``input_trust`` produces a check that cannot fail — all
    five trust levels are allowed, including ``derived_untrusted``. Reproduced
    before applying the correction:

    ===========================  =================  ===============================
    request ``input_trust``      ceiling from call  ceiling declared statically
    ===========================  =================  ===============================
    ``trusted_control``          allow              allow
    ``application``              allow              allow
    ``user_supplied``            allow              allow
    ``external_untrusted``       allow              allow
    ``derived_untrusted``        allow              **deny** ``input_trust_exceeds_grant``
    ===========================  =================  ===============================

    ``derived_untrusted`` is model-rewritten text. Letting it reach a network
    tool means a model can put content it generated — from a poisoned source it
    just read — into an outbound query. That is the indirect-injection path this
    wave's threat model names, and a call-derived ceiling permits it while
    appearing to run a capability check.

    ``tool_versions`` has the same property: ``(spec.version,)`` taken from the
    call can never mismatch, while a declared tuple can (verified:
    ``tool_version_not_granted``).
    """

    sensitivity: SensitivityClass
    max_input_trust: TrustClassification
    tool_versions: tuple[str, ...]


def self_issued_grant(
    *,
    tool_name: str,
    policy: SelfIssuedPolicy,
    identity: ToolCallIdentity,
    now: datetime,
    ttl: timedelta = DEFAULT_GRANT_TTL,
) -> CapabilityGrant:
    """Mint the interim grant described in this module's docstring.

    Takes a :class:`SelfIssuedPolicy` rather than loose values so that a caller
    cannot pass the request's own parameters back in as its own ceiling. The
    grant is then "a default policy nobody authorized" rather than a rubber
    stamp shaped exactly like the request.

    Raises:
        ValueError: the policy's ``sensitivity`` requires approval. A grant
            nobody issued must never be what permits an external write or an
            exfiltration, and refusing here fails earlier and names the tool.
            The contract enforces the same thing independently — see the test.
    """

    if policy.sensitivity in APPROVAL_REQUIRED_SENSITIVITIES:
        raise ValueError(
            f"tool {tool_name!r} is {policy.sensitivity.value}, which always "
            "requires approval; a self-issued grant cannot authorize it. Inject "
            "a grant from a real issuer instead."
        )
    return CapabilityGrant(
        grant_id=uuid.uuid4().hex,
        run_id=identity.run_id,
        task_id=identity.task_id,
        capability_scope=f"{SELF_ISSUED_SCOPE_PREFIX}{tool_name}",
        tool_name=tool_name,
        tool_versions=policy.tool_versions,
        sensitivity=policy.sensitivity,
        max_input_trust=policy.max_input_trust,
        requires_approval=False,
        issued_at=now,
        expires_at=now + ttl,
    )


@final
@dataclass(slots=True)
class InMemoryToolAuditStore:
    """A ``ToolAuditStore`` that keeps records for this process only.

    Satisfies the protocol exactly, including the idempotent-replay lookup, so
    a caller gets real replay behavior within a process. It is not durable, and
    the name is the whole point: a deployment that wants durable tool
    provenance injects a session-backed store rather than getting one by
    default without noticing.
    """

    invocations: list[ToolInvocation] = field(default_factory=list)
    events: list[ToolAuditEvent] = field(default_factory=list)
    decisions: list[CapabilityDecision | None] = field(default_factory=list)
    """Positionally aligned with :attr:`invocations`.

    Kept alongside rather than merged in, because the decision is not part of
    the invocation contract and must not start looking like it is. ``None``
    entries are the malformed-request rows, which have no decision to record.
    """
    _organization_by_invocation: dict[str, str | None] = field(
        default_factory=dict, repr=False
    )
    _reservations: dict[tuple[str, str, str | None, str], ToolInvocation] = field(
        default_factory=dict, repr=False
    )

    @staticmethod
    def _reservation_key(
        invocation: ToolInvocation, organization_id: str | None
    ) -> tuple[str, str, str | None, str]:
        return (
            invocation.run_id,
            invocation.attempt_id,
            _organization_key(organization_id),
            invocation.idempotency_key,
        )

    async def reserve_invocation(
        self,
        *,
        invocation: ToolInvocation,
        organization_id: str | None,
    ) -> ToolInvocation | None:
        """Atomically claim a request key before the boundary dispatches.

        This method intentionally has no await point before the dictionary
        claim.  A single asyncio event loop cannot interleave another task in
        that synchronous section, so the first caller wins without holding a
        lock around persistence or handler execution.
        """

        key = self._reservation_key(invocation, organization_id)
        existing = self._reservations.get(key)
        if existing is not None:
            return existing

        for recorded in reversed(self.invocations):
            if (
                self._reservation_key(recorded, organization_id) == key
                and recorded.status in _REPLAYABLE_TERMINAL_STATUSES | _PENDING_STATUSES
                and self._organization_by_invocation.get(recorded.tool_invocation_id)
                == _organization_key(organization_id)
            ):
                self._reservations[key] = recorded
                return recorded

        self._reservations[key] = invocation
        return None

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        """Match the durable store's scope: run, attempt, and key.

        ``attempt_id`` is part of the match because uniqueness in
        ``agent_tool_invocations`` is attempt-scoped. Matching on the run
        alone made a caller-supplied key from one attempt replay into the
        next, which defeats the point of an attempt-level retry.
        """
        for recorded in reversed(self.invocations):
            if (
                recorded.run_id == run_id
                and recorded.attempt_id == attempt_id
                and recorded.idempotency_key == idempotency_key
                and recorded.status in _REPLAYABLE_TERMINAL_STATUSES
                and self._organization_by_invocation.get(recorded.tool_invocation_id)
                == _organization_key(organization_id)
            ):
                return recorded
        return None

    async def find_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        """Return a request that was admitted but has not terminated."""
        for recorded in reversed(self.invocations):
            if (
                recorded.run_id == run_id
                and recorded.attempt_id == attempt_id
                and recorded.idempotency_key == idempotency_key
                and recorded.status in _PENDING_STATUSES
                and self._organization_by_invocation.get(recorded.tool_invocation_id)
                == _organization_key(organization_id)
            ):
                return recorded
        return None

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        self.invocations.append(invocation)
        self.events.extend(events)
        self.decisions.append(capability_decision)
        self._organization_by_invocation[invocation.tool_invocation_id] = (
            _organization_key(organization_id)
        )
        if invocation.status in _REPLAYABLE_TERMINAL_STATUSES | _PENDING_STATUSES:
            self._reservations[self._reservation_key(invocation, organization_id)] = (
                invocation
            )


def build_tool_boundary(
    *,
    secret_provider: SecretProvider | None = None,
    audit_store: ToolAuditStore | None = None,
    event_publisher: ToolEventPublisher | None = None,
    clock: Any = None,
) -> ToolBoundary:
    """Assemble a boundary with defaults that are visible rather than implied.

    Each default is a ``Null``/``InMemory`` collaborator whose name states what
    it does not do. 4C deliberately gave ``ToolBoundary`` no default secret
    provider, because a silently-omitted one loses redaction's exact-value
    layer with no error; this preserves that intent by naming the choice here
    once instead of at every construction site.
    """

    return ToolBoundary(
        secret_provider=secret_provider or NullSecretProvider(),
        audit_store=audit_store or InMemoryToolAuditStore(),
        event_publisher=event_publisher or NullEventPublisher(),
        clock=clock or (lambda: datetime.now(UTC)),
    )


def plain(value: Any) -> Any:
    """Return ``value`` rebuilt from plain, mutable containers.

    The boundary freezes what it records — nested mappings come back as
    ``mappingproxy`` — which is right for a durable record and wrong for a
    caller contract that previously handed back an ordinary ``dict``. Callers
    that mutate or serialize the result would break on the difference, so the
    projection back to a caller undoes it rather than leaking a record's
    immutability into an unrelated API.
    """

    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    return value


__all__ = [
    "ATTEMPT_ID_CONTEXT_KEY",
    "DEFAULT_GRANT_TTL",
    "ORGANIZATION_ID_CONTEXT_KEY",
    "RUN_ID_CONTEXT_KEY",
    "SELF_ISSUED_SCOPE_PREFIX",
    "TASK_ID_CONTEXT_KEY",
    "UNBOUND_RUN_PREFIX",
    "InMemoryToolAuditStore",
    "SelfIssuedPolicy",
    "ToolCallIdentity",
    "build_tool_boundary",
    "plain",
    "self_issued_grant",
]
