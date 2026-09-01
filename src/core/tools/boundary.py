"""The one place a tool call is mediated.

Everything a tool call needs to be safe happens here, in one order, for every
tool: schema validation, capability authorization, redaction, deadline,
cancellation, circuit breaking, idempotency, provenance, and a durable audit
record written before anything is published. A new tool inherits all of it by
being registered. There is no second path, which is the point — packet 4-Char
characterized three unmediated ones, and the defects it found were not bugs in
any single tool but consequences of every tool being on its own.

**Where the enforcement actually lives.** Four things packet 4A could specify
but not enforce are enforced here, and each by *structure* rather than by an
assertion a future edit could delete without anything noticing:

- A prompt binding arrives only inside a self-verifying
  :class:`~src.core.tools.prompts.RenderedPrompt`, whose claimed identity is
  then checked against the run's pin or the prompt registry. There is no
  parameter through which a bare digest can be supplied, and a boundary with no
  verifier refuses prompts outright rather than settling for self-consistency.
- Credential parameters are ``SecretRef``-typed, checked when the tool is
  registered rather than when it is called.
- ``now`` is read from the injected clock exactly once per call and is not a
  parameter of any public method, so a caller cannot supply a stale instant and
  revive an expired approval.
- The persisted ``capability_scope`` is read off the grant the decision named,
  never off the caller's request. This holds on *every* path that writes a
  record, including a rejected input — it did not, and the gap is described
  below.

**Authorization precedes every durable effect.** :meth:`ToolBoundary.invoke`
prepares the caller's input first, because a ``CapabilityRequest`` cannot be
fingerprinted without a digest of it. Preparing it writes nothing, publishes
nothing, and returns nothing. Every path then reaches ``self._decide`` before
any record is persisted, any event is published, or any ``ToolOutcome`` is
returned, and a denial takes precedence over a schema rejection.

That ordering replaces one where a malformed or unrepresentable payload
returned a recorded ``INVALID_INPUT`` outcome with ``decision=None`` — so a
caller holding no grant at all could read the tool's input schema back out of
``ToolOutcome.detail``, write a durable row under an idempotency key of its own
choosing carrying a ``capability_scope`` it invented, and (because that row
records as ``FAILED``, which is replay-eligible) leave it where a later
*authorized* call presenting the same key was answered with a raised
``IdempotencyConflictError`` instead of its result. The check was individually
correct and sat in the wrong place relative to what it guards.

**Fail closed, and never into a value.** A denial, a timeout, an open breaker,
and a tool that raised are four distinct outcomes, none of which carries a
result body. Where the boundary cannot form a defensible answer at all — a
decision naming a grant nobody supplied — it raises rather than returning
anything a caller could mistake for one. A raise is the fourth distinct
outcome: it persists nothing and publishes nothing, which is why the paths that
raise *before* a decision (an unregistered tool, a prompt whose identity cannot
be checked, a naive clock) do not weaken the ordering guarantee above. What
they do disclose is stated in the packet handoff rather than left to be
inferred.
"""

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Final, cast, final

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from src.core.contracts.base import JsonObject
from src.core.contracts.capabilities import (
    ApprovalRef,
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityGrant,
    CapabilityRequest,
    decide_capability,
)
from src.core.contracts.pinning import PinnedVersions
from src.core.contracts.provenance import (
    ProducerKind,
    ToolInvocation,
    ToolInvocationStatus,
)
from src.core.contracts.redaction import SecretRef, boundary_digest, redact
from src.core.contracts.trust import TrustClassification, propagate_trust

from .audit import (
    EVENT_COMPLETED,
    EVENT_REQUESTED,
    LeaseAwareToolAuditStore,
    PendingToolAuditStore,
    ToolAdmissionStore,
    ToolAuditEvent,
    ToolAuditStore,
    ToolEventPublisher,
)
from .circuit import BreakerRegistry
from .errors import (
    CapabilityDecisionUnusableError,
    IdempotencyConflictError,
    PromptBindingRefusedError,
    ToolInvocationConflictError,
    ToolNotRegisteredError,
    ToolSpecError,
)
from .outcome import _MINT as _OUTCOME_MINT
from .outcome import (
    ERROR_CODES,
    RETRY_DISPOSITIONS,
    RetryDisposition,
    ToolOutcome,
    ToolOutcomeStatus,
    invocation_status_for,
)
from .prompts import PromptIdentityVerifier, RenderedPrompt
from .secrets import SecretProvider
from .spec import ToolCallContext, ToolSpec

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
CapabilityDecider = Callable[..., CapabilityDecision]

_PRODUCER: Final[str] = "tool_boundary"

_REPLAYABLE_RECORD_STATUSES: Final[frozenset[ToolInvocationStatus]] = frozenset(
    {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
    }
)
"""Terminal records that answer for a later identical request.

``DENIED`` is deliberately **excluded**, and it is a security exclusion rather
than a tidiness one. A denial records a *decision*, not work that was done, and
decisions are now re-made on every call. Serving a recorded denial would let one
caller's refusal become another's answer: an unauthorized attempt writes a
denial under a key, and the authorized caller who later presents that key is
handed the refusal instead of their result. Authorization is fresh every time,
so its outcome must be too.

The rest are records whose outcome is a property of the request rather than of
the caller: presenting the same key for the same content gets the same answer,
which is exactly what an idempotency key exists to provide.

Two of them did *not* reach the tool, so an earlier version of this docstring —
"records of a call that actually reached the tool" — was wrong about them.
``INVALID_INPUT`` and ``CIRCUIT_OPEN`` both record as ``FAILED``. Keeping
``INVALID_INPUT`` is deliberate: a payload that does not parse does not parse on
the retry either, so replaying is the same answer, and excluding it would send
an identical repeat down the execute path to collide with
``UniqueConstraint(attempt_id, idempotency_key)``. ``CIRCUIT_OPEN`` is the
uncomfortable one — it is ``RETRIABLE``, yet a key that recorded it is answered
from that record even after the breaker closes. That is a live defect, reported
rather than fixed here because it belongs with the breaker's own semantics and
not with this packet's ordering change.
"""

_FAILED_STATUS_BY_ERROR_CODE: Final[dict[str, ToolOutcomeStatus]] = {
    ERROR_CODES[status]: status
    for status in (
        ToolOutcomeStatus.INVALID_INPUT,
        ToolOutcomeStatus.INVALID_OUTPUT,
        ToolOutcomeStatus.CIRCUIT_OPEN,
        ToolOutcomeStatus.FAILED,
    )
}


class _RequestedPublicationError(Exception):
    """The requested record was persisted, but its event was not published."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause


@final
class CancellationToken:
    """Cooperative cancellation a caller can signal and the boundary can see.

    Distinct from ``asyncio`` cancellation on purpose. Cancelling a task is a
    control-flow event that unwinds the stack; this is a *decision* the run
    made, and it has to end in a durable ``CANCELLED`` record rather than an
    unwind that leaves no trace. Both are supported — the boundary records
    either — but only this one can be observed without interfering with the
    caller's own cancellation semantics.
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


@final
@dataclass(frozen=True, slots=True)
class _AcceptedInput:
    """A payload that parsed, serialized, redacted and hashed."""

    validated_input: BaseModel
    redacted_input: Mapping[str, JsonValue]
    input_sha256: str


@final
@dataclass(frozen=True, slots=True)
class _RejectedInput:
    """A payload that did not, and the redacted reason it did not.

    It still carries a redacted payload and a digest, because the call is
    authorized before it is refused and the refusal is recorded. It carries no
    ``validated_input`` field at all, which is the point: there is no way to
    reach :meth:`ToolBoundary._run_tool` holding one of these, so the branch
    that stops an unusable payload from reaching a handler is a type distinction
    rather than a ``None`` check some later edit could drop.
    """

    redacted_input: Mapping[str, JsonValue]
    input_sha256: str
    reason: str


_PreparedInput = _AcceptedInput | _RejectedInput


@final
@dataclass(frozen=True, slots=True)
class _Call:
    """Everything about one invocation that every outcome record shares."""

    spec: ToolSpec
    tool_invocation_id: str
    run_id: str
    task_id: str
    attempt_id: str
    organization_id: str | None
    capability_scope: str
    idempotency_key: str
    redacted_input: Mapping[str, JsonValue]
    input_trust: TrustClassification
    producer_kind: ProducerKind
    prompt: RenderedPrompt | None
    requested_at: datetime
    secret_values: frozenset[str]
    lease_owner_id: str
    lease_duration: timedelta
    lease_expires_at: datetime


@final
@dataclass(slots=True)
class _LeaseMonitor:
    """Lifecycle state shared by the heartbeat and the active invocation."""

    admitted: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    lost: asyncio.Event = field(default_factory=asyncio.Event)
    failure_detail: str | None = None
    task: asyncio.Task[None] | None = None


@final
class _LeaseRenewalFailureError(RuntimeError):
    """The active call lost its durable lease and must not return output."""


_LEASE_GRACE_MIN_SECONDS: Final[float] = 1.0
_LEASE_GRACE_RATIO: Final[float] = 0.25
_LEASE_HEARTBEAT_MIN_SECONDS: Final[float] = 0.01
_LEASE_HEARTBEAT_MAX_SECONDS: Final[float] = 0.25
_HANDLER_CANCELLATION_GRACE_SECONDS: Final[float] = 0.05


@final
class ToolBoundary:
    """The mediator every tool call passes through."""

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        audit_store: ToolAuditStore,
        event_publisher: ToolEventPublisher,
        clock: Clock,
        breakers: BreakerRegistry | None = None,
        decide: CapabilityDecider = decide_capability,
        id_factory: IdFactory | None = None,
        event_destinations: Sequence[str] = (),
        prompt_verifier: PromptIdentityVerifier | None = None,
    ) -> None:
        """Wire the boundary's dependencies.

        ``secret_provider`` has no default. Packet 4A reported that ``redact``
        silently loses its exact-value layer when its held-secret argument is
        omitted, and a default here would be that same omission wearing a
        different hat. A deployment holding no credentials passes
        :class:`~src.core.tools.secrets.NullSecretProvider` and says so.

        ``decide`` is injected so the boundary's behavior against a decision it
        cannot act on is reachable in a test. It is not a policy hook: the
        default is the frozen contract function, and overriding it does not
        widen what the boundary will execute — every guard downstream still
        runs.
        """

        self._secret_provider = secret_provider
        self._audit_store = audit_store
        self._event_publisher = event_publisher
        self._clock = clock
        self._breakers = breakers if breakers is not None else BreakerRegistry()
        self._decide = decide
        self._id_factory = id_factory if id_factory is not None else _uuid4_hex
        self._event_destinations = tuple(event_destinations)
        self._prompt_verifier = prompt_verifier
        self._specs: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        """Admit a tool, rejecting an unsound specification here and not later."""

        existing = self._specs.get(spec.name)
        if existing is not None and existing.version != spec.version:
            raise ToolSpecError(
                f"tool {spec.name!r} is already registered at version "
                f"{existing.version!r}; register a distinct name rather than "
                "shadowing a version a run may already have been authorized for"
            )
        self._specs[spec.name] = spec

    def specification(self, tool_name: str) -> ToolSpec:
        try:
            return self._specs[tool_name]
        except KeyError:
            raise ToolNotRegisteredError(
                f"no tool is registered as {tool_name!r}"
            ) from None

    # ------------------------------------------------------------------
    # invocation
    # ------------------------------------------------------------------

    async def invoke(
        self,
        *,
        tool_name: str,
        run_id: str,
        task_id: str,
        attempt_id: str,
        organization_id: str | None,
        capability_scope: str,
        arguments: Mapping[str, Any],
        input_trust: TrustClassification,
        grants: Sequence[CapabilityGrant],
        approvals: Sequence[ApprovalRef] = (),
        output_trust: TrustClassification | None = None,
        idempotency_key: str | None = None,
        prompt: RenderedPrompt | None = None,
        pinned_versions: PinnedVersions | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ToolOutcome:
        """Execute one tool call under the full boundary.

        There is deliberately **no** ``now`` parameter. Packet 4A left approval
        expiry on a caller-supplied instant so decisions replay
        deterministically, and noted that a caller passing a stale ``now``
        revives an expired approval. Determinism is preserved by injecting the
        clock at construction; the instant itself is read here, once, and used
        for the authorization decision, the breaker, and every timestamp on the
        record, so no two of them can disagree.
        """

        now = self._now()
        spec = self.specification(tool_name)
        verified_prompt = self._verify_prompt(prompt, pinned_versions)
        secret_values = self._secret_provider.secret_values()

        # Preparing the input comes first and *only* first: it computes values,
        # and on no path does it write, publish, or return. A capability request
        # cannot be built without `input_sha256`, so the digest has to exist
        # before a decision can be made — but a rejection discovered while
        # computing it is carried forward as data rather than acted on here.
        # Acting on it here is precisely the defect: two `return` statements
        # stood between this point and `self._decide`.
        prepared = self._prepare_input(
            spec=spec, arguments=arguments, secret_values=secret_values
        )
        effective_key = idempotency_key or self._derive_idempotency_key(
            spec=spec,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            capability_scope=capability_scope,
            input_sha256=prepared.input_sha256,
        )

        tool_invocation_id = self._id_factory()
        lease_duration = _lease_duration(spec.timeout_seconds)
        call = _Call(
            spec=spec,
            tool_invocation_id=tool_invocation_id,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            capability_scope=capability_scope,
            idempotency_key=effective_key,
            redacted_input=prepared.redacted_input,
            input_trust=input_trust,
            producer_kind=(
                ProducerKind.SYSTEM
                if verified_prompt is None
                else ProducerKind.MODEL_TURN
            ),
            prompt=verified_prompt,
            requested_at=now,
            secret_values=secret_values,
            lease_owner_id=tool_invocation_id,
            lease_duration=lease_duration,
            lease_expires_at=now + lease_duration,
        )

        decision = self._decide(
            request=CapabilityRequest(
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                tool_name=spec.name,
                tool_version=spec.version,
                capability_scope=capability_scope,
                sensitivity=spec.sensitivity,
                input_trust=input_trust,
                input_sha256=prepared.input_sha256,
                requested_at=now,
            ),
            grants=grants,
            now=now,
            approvals=approvals,
        )

        if decision.effect is not CapabilityDecisionEffect.ALLOW:
            # Checking the effect, never the presence of `grant_id`: a DENY
            # decision names the grant that got furthest, so "a grant is named"
            # is true of denials too. Packet 4-Char found the mirror image of
            # this mistake in the existing integration, where `data_source` was
            # computed from a success flag alone and so mislabelled a
            # fabricated fallback as a real tool result.
            #
            # A denial outranks a schema rejection, and the precedence is the
            # disclosure control. `detail` here names the authorization reason
            # and nothing else — never the pydantic report, which lists the
            # tool's field names, their types, and the caller's own values back
            # to a caller that was never permitted to call the tool. A caller
            # who could tell "denied" from "denied, and by the way your third
            # argument is the wrong type" would have a schema oracle for every
            # tool it cannot reach.
            return await self._terminate(
                call,
                status=ToolOutcomeStatus.DENIED,
                completed_at=now,
                decision=decision,
                detail=(
                    None
                    if decision.denial_reason is None
                    else decision.denial_reason.value
                ),
            )

        # 4A's non-guarantee 7: the scope written to the record comes off the
        # grant the decision named, not off the caller's request. They coincide
        # whenever `decide_capability` allows, because its identity match
        # requires them equal — so this is not a behavioral difference today.
        # It is a provenance one: the recorded scope is sourced from an object
        # that actually authorized the call.
        grant = self._resolve_grant(decision, grants)
        call = replace(call, capability_scope=grant.capability_scope)

        # The dedup lookup happens **here**, downstream of the authorization
        # decision, and the ordering is the security property.
        #
        # It used to run before `self._decide`, keyed on run and idempotency key
        # alone. Any caller who could choose a key could therefore present one
        # from an earlier authorized call, name a different tool under an
        # escalated scope, supply no grant, and be handed the earlier result as
        # a success — because the `return` fired before authorization was ever
        # reached. A check placed after a `return` is not a check.
        #
        # Comparing the recorded request's identity (below) closes the same hole
        # a second way, but only this ordering makes the bypass unreachable.
        # Two later `return`s were found still standing above `self._decide`,
        # on the input-rejection paths, which is why the invariant is now stated
        # as a property of `invoke` as a whole and pinned by
        # `test_authorization_precedes_every_effect.py` rather than asserted in
        # a comment next to one of the returns it is about.
        if isinstance(self._audit_store, LeaseAwareToolAuditStore):
            # The lease-aware seam is the single lookup for both terminal
            # replay and pending recovery. In particular, a stale request is
            # terminalized by the store before this call sees it; executing
            # after a lease expires would duplicate a side effect whose result
            # is no longer knowable.
            recovered = await self._audit_store.reconcile_pending_invocation(
                run_id=run_id,
                attempt_id=attempt_id,
                organization_id=organization_id,
                idempotency_key=effective_key,
                now=now,
            )
            if recovered is not None:
                self._require_same_request(
                    recovered,
                    spec=spec,
                    capability_scope=call.capability_scope,
                    input_sha256=prepared.input_sha256,
                )
                if recovered.status in _REPLAYABLE_RECORD_STATUSES:
                    return self._replay(recovered, decision=decision)
                if recovered.status in {
                    ToolInvocationStatus.REQUESTED,
                    ToolInvocationStatus.RUNNING,
                }:
                    return self._in_progress(recovered, decision=decision)

        else:
            replayed = await self._audit_store.find_invocation(
                run_id=run_id,
                attempt_id=attempt_id,
                organization_id=organization_id,
                idempotency_key=effective_key,
            )
            if replayed is not None and replayed.status in _REPLAYABLE_RECORD_STATUSES:
                self._require_same_request(
                    replayed,
                    spec=spec,
                    capability_scope=call.capability_scope,
                    input_sha256=prepared.input_sha256,
                )
                return self._replay(replayed, decision=decision)

            # A terminal replay answers a completed request. A committed
            # nonterminal row answers a different question: whether this retry
            # is allowed to start a second unit of work. It is not. Only stores
            # that implement the optional recovery seam can answer it; older
            # adapters remain valid and retain their prior behavior until
            # upgraded.
            if isinstance(self._audit_store, PendingToolAuditStore):
                pending = await self._audit_store.find_pending_invocation(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    organization_id=organization_id,
                    idempotency_key=effective_key,
                )
                if pending is not None:
                    self._require_same_request(
                        pending,
                        spec=spec,
                        capability_scope=call.capability_scope,
                        input_sha256=prepared.input_sha256,
                    )
                    return self._in_progress(pending, decision=decision)

        if isinstance(prepared, _RejectedInput):
            # The input rejection lands here, after authorization and after
            # dedup but before the breaker. After dedup, so an authorized
            # caller repeating one malformed request is answered from its own
            # record instead of being told its key conflicts with itself.
            # Before the breaker, because a payload that cannot parse cannot
            # parse whatever the dependency's health is, and `CIRCUIT_OPEN` is
            # `RETRIABLE` — telling a caller to retry a permanently invalid
            # request is the wrong answer, not merely a less precise one.
            return await self._terminate(
                call,
                status=ToolOutcomeStatus.INVALID_INPUT,
                completed_at=now,
                decision=decision,
                detail=prepared.reason,
            )

        breaker = self._breakers.for_tool(spec.name)
        if not breaker.allows(now):
            return await self._terminate(
                call,
                status=ToolOutcomeStatus.CIRCUIT_OPEN,
                completed_at=now,
                decision=decision,
                detail=f"circuit open for {spec.name!r}",
            )

        requested_invocation = ToolInvocation(
            tool_invocation_id=call.tool_invocation_id,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_name=spec.name,
            tool_version=spec.version,
            status=ToolInvocationStatus.REQUESTED,
            capability_scope=call.capability_scope,
            idempotency_key=effective_key,
            input=prepared.redacted_input,
            input_trust=input_trust,
            producer_kind=call.producer_kind,
            prompt_binding=(
                None if verified_prompt is None else verified_prompt.binding
            ),
            requested_at=now,
            lease_owner_id=call.lease_owner_id,
            lease_expires_at=call.lease_expires_at,
        )
        if isinstance(self._audit_store, ToolAdmissionStore):
            existing = await self._audit_store.reserve_invocation(
                invocation=requested_invocation,
                organization_id=organization_id,
            )
            if existing is not None:
                self._require_same_request(
                    existing,
                    spec=spec,
                    capability_scope=call.capability_scope,
                    input_sha256=prepared.input_sha256,
                )
                if existing.status in _REPLAYABLE_RECORD_STATUSES:
                    return self._replay(existing, decision=decision)
                if existing.status in {
                    ToolInvocationStatus.REQUESTED,
                    ToolInvocationStatus.RUNNING,
                }:
                    return self._in_progress(existing, decision=decision)
                raise ToolInvocationConflictError(existing)

        lease_monitor = self._start_lease_monitor(call)
        requested_record = asyncio.create_task(
            self._record(
                call,
                invocation=requested_invocation,
                event_type=EVENT_REQUESTED,
                payload={
                    "tool_name": spec.name,
                    "tool_version": spec.version,
                    "capability_scope": call.capability_scope,
                    "grant_id": grant.grant_id,
                    "input_sha256": prepared.input_sha256,
                    "input_trust": input_trust.value,
                },
                decision=decision,
                wrap_publication_failure=True,
                lease_monitor=lease_monitor,
            )
        )
        try:
            try:
                await asyncio.shield(requested_record)
            except asyncio.CancelledError:
                # The requested row is the idempotency reservation. It must finish
                # its persist-and-publish admission before cancellation can unwind,
                # otherwise a durable REQUESTED row can be left without a terminal
                # outcome (or the request can be retried while its reservation is
                # still in flight). The cancellation wins over an admission
                # publication error: the reservation is settled as CANCELLED and
                # the caller's cancellation is re-raised.
                try:
                    await asyncio.shield(requested_record)
                except _RequestedPublicationError:
                    pass
                except asyncio.CancelledError:
                    # A publisher may use CancelledError to reject the requested
                    # event. It is still an admission failure that must be
                    # settled before preserving the cancellation for the caller.
                    pass
                except Exception:
                    # A persistence failure means there is no confirmed requested
                    # row to settle. Preserve the caller's cancellation; the store
                    # owns the failed admission's rollback semantics.
                    pass
                await asyncio.shield(
                    self._terminate(
                        call,
                        status=ToolOutcomeStatus.CANCELLED,
                        completed_at=self._now(),
                        decision=decision,
                        detail="cancelled during admission",
                    )
                )
                raise
            except _RequestedPublicationError as publication_error:
                return await self._terminate(
                    call,
                    status=ToolOutcomeStatus.FAILED,
                    completed_at=self._now(),
                    decision=decision,
                    detail=str(publication_error),
                )
            except ToolInvocationConflictError as conflict:
                self._require_same_request(
                    conflict.invocation,
                    spec=spec,
                    capability_scope=call.capability_scope,
                    input_sha256=prepared.input_sha256,
                )
                if conflict.invocation.status in _REPLAYABLE_RECORD_STATUSES:
                    return self._replay(conflict.invocation, decision=decision)
                if conflict.invocation.status in {
                    ToolInvocationStatus.REQUESTED,
                    ToolInvocationStatus.RUNNING,
                }:
                    return self._in_progress(conflict.invocation, decision=decision)
                raise

            return await self._run_tool(
                call,
                validated_input=prepared.validated_input,
                declared_output_trust=output_trust,
                decision=decision,
                cancellation=cancellation,
                lease_monitor=lease_monitor,
            )
        finally:
            await self._stop_lease_monitor(lease_monitor)

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------

    async def _run_tool(
        self,
        call: _Call,
        *,
        validated_input: BaseModel,
        declared_output_trust: TrustClassification | None,
        decision: CapabilityDecision,
        cancellation: CancellationToken | None,
        lease_monitor: _LeaseMonitor | None,
    ) -> ToolOutcome:
        spec = call.spec
        breaker = self._breakers.for_tool(spec.name)
        context = ToolCallContext(
            run_id=call.run_id,
            task_id=call.task_id,
            attempt_id=call.attempt_id,
            tool_invocation_id=call.tool_invocation_id,
            resolve_secret=self._resolve_secret,
        )

        try:
            status, payload, detail, error = await self._call_handler(
                spec,
                validated_input,
                context,
                cancellation,
                lease_monitor,
            )
        except asyncio.CancelledError:
            # Give the half-open trial slot back before unwinding. The call is
            # over either way, and a slot consumed by a probe nobody is waiting
            # for any more is a slot no later caller can ever take.
            breaker.record_abandoned()
            # An external cancellation still leaves a durable record. Shielded
            # so the write survives the unwinding, then re-raised: swallowing
            # CancelledError would break the caller's own cancellation.
            await asyncio.shield(
                self._terminate(
                    call,
                    status=ToolOutcomeStatus.CANCELLED,
                    completed_at=self._now(),
                    decision=decision,
                    detail="cancelled by the event loop",
                )
            )
            raise

        if lease_monitor is not None and lease_monitor.lost.is_set():
            status = ToolOutcomeStatus.FAILED
            payload = None
            detail = lease_monitor.failure_detail or "durable lease renewal failed"
            error = _LeaseRenewalFailureError(detail)

        completed_at = self._now()

        if status is ToolOutcomeStatus.SUCCEEDED:
            try:
                validated_output = spec.output_model.model_validate(payload)
            except ValidationError as output_error:
                # Schema drift or a poisoned response. Distinct from a tool
                # that raised, and never passed through: an unvalidated body
                # reaching a caller under a validated tool's name is the whole
                # failure this boundary exists to prevent.
                breaker.record_failure(completed_at)
                return await self._terminate(
                    call,
                    status=ToolOutcomeStatus.INVALID_OUTPUT,
                    completed_at=completed_at,
                    decision=decision,
                    detail=_redact_text(str(output_error), call.secret_values),
                )

            breaker.record_success()
            return await self._terminate(
                call,
                status=ToolOutcomeStatus.SUCCEEDED,
                completed_at=completed_at,
                decision=decision,
                detail=None,
                output=_redact_object(
                    _json_ready(validated_output), call.secret_values
                ),
                output_trust=(
                    declared_output_trust
                    if declared_output_trust is not None
                    else propagate_trust((call.input_trust,))
                ),
            )

        if status is ToolOutcomeStatus.CANCELLED:
            # A withdrawn request says nothing about the dependency's health,
            # so cancellation alone must not push the breaker toward opening.
            # It is conclusive about the trial slot, though: the probe is over,
            # and holding the slot would refuse every later call for the
            # lifetime of the process.
            breaker.record_abandoned()
        else:
            breaker.record_failure(completed_at)

        return await self._terminate(
            call,
            status=status,
            completed_at=completed_at,
            decision=decision,
            detail=detail,
            retry=(
                (
                    RetryDisposition.TERMINAL
                    if isinstance(error, _LeaseRenewalFailureError)
                    else spec.disposition_for(error)
                )
                if error is not None and status is ToolOutcomeStatus.FAILED
                else None
            ),
        )

    async def _call_handler(
        self,
        spec: ToolSpec,
        validated_input: BaseModel,
        context: ToolCallContext,
        cancellation: CancellationToken | None,
        lease_monitor: _LeaseMonitor | None,
    ) -> tuple[
        ToolOutcomeStatus, Mapping[str, Any] | None, str | None, BaseException | None
    ]:
        """Run the handler under its deadline and the caller's cancellation.

        Timeout and cancellation are raced rather than nested so they stay
        distinguishable: nesting them makes a cancellation arriving in the
        final moments of a deadline indistinguishable from the deadline, and
        telling those apart is exactly what a caller needs.
        """

        if cancellation is not None and cancellation.cancelled:
            return ToolOutcomeStatus.CANCELLED, None, "cancelled before dispatch", None

        if lease_monitor is not None and lease_monitor.lost.is_set():
            detail = lease_monitor.failure_detail or "durable lease renewal failed"
            return (
                ToolOutcomeStatus.FAILED,
                None,
                detail,
                _LeaseRenewalFailureError(detail),
            )

        handler_task: asyncio.Task[Mapping[str, Any]] = asyncio.ensure_future(
            spec.handler(validated_input, context)
        )
        watchers: list[asyncio.Future[Any]] = [handler_task]
        cancel_task: asyncio.Task[None] | None = None
        lease_lost_task: asyncio.Task[bool] | None = None
        if cancellation is not None:
            cancel_task = asyncio.ensure_future(cancellation.wait())
            watchers.append(cancel_task)
        if lease_monitor is not None:
            lease_lost_task = asyncio.ensure_future(lease_monitor.lost.wait())
            watchers.append(lease_lost_task)

        try:
            done, _pending = await asyncio.wait(
                watchers,
                timeout=spec.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            # The enclosing coroutine is being torn down -- most often an
            # external cancellation, which arrives here rather than through
            # the token. Nothing below runs, so without this the handler task
            # outlives the call that started it: the boundary records
            # CANCELLED and the tool goes on to complete its side effect,
            # which for an external write is the effect happening after the
            # system reported that it would not.
            #
            # Settle the handler before propagating the cancellation. A
            # handler is allowed to catch CancelledError for cleanup; merely
            # calling `cancel()` and recording CANCELLED would let that
            # cleanup continue after the durable record claimed the call had
            # ended.
            await _abandon(handler_task)
            raise
        finally:
            if cancel_task is not None and not cancel_task.done():
                await _abandon(cancel_task)
            if lease_lost_task is not None and not lease_lost_task.done():
                await _abandon(lease_lost_task)

        if lease_monitor is not None and (
            lease_lost_task in done or lease_monitor.lost.is_set()
        ):
            await _abandon(handler_task)
            detail = lease_monitor.failure_detail or "durable lease renewal failed"
            return (
                ToolOutcomeStatus.FAILED,
                None,
                detail,
                _LeaseRenewalFailureError(detail),
            )

        if handler_task in done:
            error = handler_task.exception()
            if error is not None:
                return (
                    ToolOutcomeStatus.FAILED,
                    None,
                    f"{type(error).__name__}: {error}",
                    error,
                )
            return ToolOutcomeStatus.SUCCEEDED, handler_task.result(), None, None

        await _abandon(handler_task)

        if cancel_task is not None and cancel_task in done:
            return ToolOutcomeStatus.CANCELLED, None, "cancelled by caller", None
        return (
            ToolOutcomeStatus.TIMED_OUT,
            None,
            f"exceeded {spec.timeout_seconds}s deadline",
            None,
        )

    def _start_lease_monitor(self, call: _Call) -> _LeaseMonitor | None:
        """Start renewal only for stores that can durably enforce the lease."""

        if not isinstance(self._audit_store, LeaseAwareToolAuditStore):
            return None
        monitor = _LeaseMonitor()
        monitor.task = asyncio.create_task(
            self._lease_heartbeat(self._audit_store, call, monitor)
        )
        return monitor

    async def _stop_lease_monitor(self, monitor: _LeaseMonitor | None) -> None:
        """Stop and await the heartbeat after the invocation is settled."""

        if monitor is None or monitor.task is None:
            return
        monitor.stop.set()
        monitor.task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await monitor.task

    async def _lease_heartbeat(
        self,
        store: LeaseAwareToolAuditStore,
        call: _Call,
        monitor: _LeaseMonitor,
    ) -> None:
        """Renew a live invocation until settlement or a lease failure."""

        while not monitor.admitted.is_set():
            admitted = asyncio.ensure_future(monitor.admitted.wait())
            stopped = asyncio.ensure_future(monitor.stop.wait())
            try:
                done, _pending = await asyncio.wait(
                    (admitted, stopped), return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                return
            finally:
                for task in (admitted, stopped):
                    if not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task

            if stopped in done or monitor.stop.is_set():
                return

        interval = min(
            max(
                _LEASE_HEARTBEAT_MIN_SECONDS,
                call.spec.timeout_seconds / 3,
            ),
            _LEASE_HEARTBEAT_MAX_SECONDS,
        )
        while not monitor.stop.is_set():
            try:
                await asyncio.wait_for(monitor.stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass

            if monitor.stop.is_set():
                return

            try:
                now = self._now()
                renewed = await store.renew_invocation_lease(
                    tool_invocation_id=call.tool_invocation_id,
                    run_id=call.run_id,
                    attempt_id=call.attempt_id,
                    organization_id=call.organization_id,
                    idempotency_key=call.idempotency_key,
                    lease_owner_id=call.lease_owner_id,
                    now=now,
                    lease_expires_at=now + call.lease_duration,
                )
            except asyncio.CancelledError:
                if monitor.stop.is_set():
                    return
                monitor.failure_detail = "durable lease renewal was cancelled"
                monitor.lost.set()
                return
            except Exception as error:
                monitor.failure_detail = (
                    f"durable lease renewal raised {type(error).__name__}"
                )
                monitor.lost.set()
                return

            if not renewed:
                monitor.failure_detail = "durable lease renewal was rejected"
                monitor.lost.set()
                return

    # ------------------------------------------------------------------
    # input preparation
    # ------------------------------------------------------------------

    def _prepare_input(
        self,
        *,
        spec: ToolSpec,
        arguments: Mapping[str, Any],
        secret_values: frozenset[str],
    ) -> _PreparedInput:
        """Parse, serialize, redact and hash the caller's payload.

        Deliberately **not** a coroutine and deliberately without access to the
        audit store: it returns a value on every path, including the ones where
        the payload is unusable, and there is no branch through it that could
        persist, publish, or answer the caller. That is what lets it run before
        the capability decision without the decision being skippable — the
        digest a :class:`CapabilityRequest` needs has to be computed first, and
        computing it is all this does.

        Preparing the input runs three separate recursive walks over the
        caller's structure — serialize, redact, hash — and then a fourth when
        the contract validates it into ``ToolInvocation``. All four can fail on
        a structure the boundary cannot represent, and all four used to sit
        outside any guard, so the same payload escaped as a ``RecursionError``,
        as a ``ValidationError``, or not at all depending on stack depth and
        interpreter configuration. ``_ensure_representable`` runs the contract's
        own validation here, at the point where the input is the suspect, so
        record construction later cannot fail for a reason that belongs here.
        """

        try:
            validated = spec.input_model.model_validate(dict(arguments))
        except ValidationError as schema_error:
            return self._reject(
                arguments=arguments, error=schema_error, secret_values=secret_values
            )

        try:
            redacted = _redact_object(_json_ready(validated), secret_values)
            _ensure_representable(redacted)
            digest = boundary_digest(redacted)
        except (ValidationError, RecursionError) as representation_error:
            return self._reject(
                arguments=arguments,
                error=representation_error,
                secret_values=secret_values,
            )

        return _AcceptedInput(
            validated_input=validated, redacted_input=redacted, input_sha256=digest
        )

    def _reject(
        self,
        *,
        arguments: Mapping[str, Any],
        error: ValidationError | RecursionError,
        secret_values: frozenset[str],
    ) -> _RejectedInput:
        """Describe an unusable payload well enough to authorize and record it.

        A rejection is carried, not acted on. It still needs a redacted payload
        and a digest, because the request is authorized before it is refused and
        a ``CapabilityRequest`` is fingerprinted over the digest — and because
        the eventual record must say what was submitted. An audit trail with a
        hole where every malformed request should be is exactly the trail an
        attacker would prefer.

        This path walks the caller's structure too, so it can fail in exactly
        the way it exists to report. When it does, a flat marker stands in for
        the payload: the offending value is by definition the thing that cannot
        be stored. The digest is taken *inside* the guard, since hashing is one
        of the walks that can fail.
        """

        try:
            redacted = _redact_object(dict(arguments), secret_values)
            _ensure_representable(redacted)
            digest = boundary_digest(redacted)
        except (ValidationError, RecursionError):
            redacted = _unprocessable_input()
            digest = boundary_digest(redacted)

        return _RejectedInput(
            redacted_input=redacted,
            input_sha256=digest,
            reason=_redact_text(str(error), secret_values) or "",
        )

    # ------------------------------------------------------------------
    # recording
    # ------------------------------------------------------------------

    async def _terminate(
        self,
        call: _Call,
        *,
        status: ToolOutcomeStatus,
        completed_at: datetime,
        detail: str | None,
        decision: CapabilityDecision | None = None,
        output: Mapping[str, JsonValue] | None = None,
        output_trust: TrustClassification | None = None,
        retry: RetryDisposition | None = None,
    ) -> ToolOutcome:
        """Build, persist, publish, and return one terminal outcome."""

        invocation = ToolInvocation(
            tool_invocation_id=call.tool_invocation_id,
            run_id=call.run_id,
            task_id=call.task_id,
            attempt_id=call.attempt_id,
            tool_name=call.spec.name,
            tool_version=call.spec.version,
            status=invocation_status_for(status),
            capability_scope=call.capability_scope,
            idempotency_key=call.idempotency_key,
            input=call.redacted_input,
            input_trust=call.input_trust,
            output=output,
            output_trust=output_trust,
            producer_kind=call.producer_kind,
            prompt_binding=None if call.prompt is None else call.prompt.binding,
            error_code=(
                None if status is ToolOutcomeStatus.SUCCEEDED else ERROR_CODES[status]
            ),
            status_reason=_redact_text(detail, call.secret_values) or None,
            requested_at=call.requested_at,
            completed_at=completed_at,
        )
        terminal_record = asyncio.create_task(
            self._record(
                call,
                invocation=invocation,
                event_type=EVENT_COMPLETED,
                payload={
                    "tool_name": call.spec.name,
                    "tool_version": call.spec.version,
                    "outcome": status.value,
                    "error_code": invocation.error_code,
                    "capability_scope": call.capability_scope,
                },
                decision=decision,
            )
        )
        try:
            await asyncio.shield(terminal_record)
        except asyncio.CancelledError:
            # A caller can cancel while the durable terminal transition is in
            # flight. Keep the record task alive and wait for its result before
            # propagating cancellation; otherwise a REQUESTED row can outlive
            # the call that already told the handler to stop.
            if not terminal_record.done():
                await asyncio.shield(terminal_record)
            raise
        except ToolInvocationConflictError as conflict:
            # A lease recovery may have terminalized this invocation while the
            # original handler was unwinding. The durable row is authoritative;
            # never return the late worker's output or overwrite the recovery
            # event. Replaying the fenced row also keeps the result fail-closed
            # when the recovery outcome is ``unknown_after_lease_expired``.
            self._require_same_request(
                conflict.invocation,
                spec=call.spec,
                capability_scope=call.capability_scope,
                input_sha256=boundary_digest(call.redacted_input),
            )
            if (
                decision is not None
                and conflict.invocation.status in _REPLAYABLE_RECORD_STATUSES
            ):
                return self._replay(conflict.invocation, decision=decision)
            if (
                conflict.invocation.status
                in {
                    ToolInvocationStatus.REQUESTED,
                    ToolInvocationStatus.RUNNING,
                }
                and decision is not None
            ):
                return self._in_progress(conflict.invocation, decision=decision)
            raise
        return ToolOutcome(
            mint=_OUTCOME_MINT,
            status=status,
            invocation=invocation,
            retry=retry if retry is not None else RETRY_DISPOSITIONS[status],
            decision=decision,
            detail=invocation.status_reason,
        )

    async def _record(
        self,
        call: _Call,
        *,
        invocation: ToolInvocation,
        event_type: str,
        payload: Mapping[str, JsonValue],
        decision: CapabilityDecision | None,
        wrap_publication_failure: bool = False,
        lease_monitor: _LeaseMonitor | None = None,
    ) -> None:
        """Persist the record and its event, then publish. Never the reverse.

        The publish is not shielded from a persistence failure — it is skipped
        by it. If ``persist`` raises, this raises, and nothing was published.
        That is the ordering guarantee: a subscriber never learns a fact the
        database does not hold.
        """

        event = ToolAuditEvent(
            event_id=self._id_factory(),
            run_id=invocation.run_id,
            task_id=invocation.task_id,
            attempt_id=invocation.attempt_id,
            aggregate_id=invocation.tool_invocation_id,
            event_type=event_type,
            occurred_at=invocation.completed_at or invocation.requested_at,
            producer=_PRODUCER,
            deduplication_key=(
                f"{invocation.tool_invocation_id}:{event_type}:"
                f"{invocation.status.value}"
            ),
            payload=dict(payload),
            destinations=self._event_destinations,
            correlation_id=invocation.run_id,
        )
        await self._audit_store.persist(
            invocation=invocation,
            events=(event,),
            organization_id=call.organization_id,
            capability_decision=decision,
        )
        if (
            lease_monitor is not None
            and invocation.status is ToolInvocationStatus.REQUESTED
        ):
            lease_monitor.admitted.set()
        try:
            await self._event_publisher.publish((event,))
        except Exception as error:
            if wrap_publication_failure:
                raise _RequestedPublicationError(error) from error
            raise

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError(
                "the boundary clock must return an aware datetime; a naive one "
                "compares incorrectly against grant and approval windows"
            )
        return now

    def _resolve_secret(self, ref: SecretRef) -> str:
        return self._secret_provider.resolve(ref.secret_id)

    def _verify_prompt(
        self, prompt: RenderedPrompt | None, pinned: PinnedVersions | None
    ) -> RenderedPrompt | None:
        """Verify a prompt's self-consistency *and* its claimed identity.

        A boundary with no verifier configured refuses any call that supplies a
        prompt, rather than falling back to consistency alone. Accepting because
        the stronger check is unavailable is how a check becomes optional in
        production while still looking present in the code.
        """

        if prompt is None:
            return None
        if self._prompt_verifier is None:
            raise PromptBindingRefusedError(
                "a prompt was supplied but this boundary has no "
                "PromptIdentityVerifier, so the prompt's identity cannot be "
                "checked against the run's pin or the registry; refusing rather "
                "than accepting self-consistency as though it were identity"
            )
        return self._prompt_verifier.verify(prompt, pinned=pinned)

    def _resolve_grant(
        self, decision: CapabilityDecision, grants: Sequence[CapabilityGrant]
    ) -> CapabilityGrant:
        """Return the grant an ``ALLOW`` named, or refuse to proceed.

        An allow naming a grant nobody supplied cannot be executed under any
        permission the boundary is able to record. There is no safe default —
        proceeding would run a tool under an unrecordable authorization — so it
        raises.
        """

        for grant in grants:
            if grant.grant_id == decision.grant_id:
                return grant
        raise CapabilityDecisionUnusableError(
            f"authorization allowed the call under grant {decision.grant_id!r}, "
            "which is not among the grants supplied; refusing to execute under "
            "a permission that cannot be recorded"
        )

    def _derive_idempotency_key(
        self,
        *,
        spec: ToolSpec,
        run_id: str,
        task_id: str,
        attempt_id: str,
        capability_scope: str,
        input_sha256: str,
    ) -> str:
        """Derive a key from everything that determines what a call does.

        Deterministic, so an identical repeat of a call is recognized as the
        same call. ``attempt_id`` is included, so a deliberate retry at the
        attempt level is a genuinely new invocation rather than a replay of the
        previous attempt's result.
        """

        return boundary_digest(
            {
                "tool_name": spec.name,
                "tool_version": spec.version,
                "run_id": run_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
                "capability_scope": capability_scope,
                "input_sha256": input_sha256,
            }
        )

    def _require_same_request(
        self,
        recorded: ToolInvocation,
        *,
        spec: ToolSpec,
        capability_scope: str,
        input_sha256: str,
    ) -> None:
        """Refuse to treat a lookup hit as a replay unless it is the same call.

        An idempotency key asserts "this is the request I made before". The
        assertion is checkable, so it is checked: same tool, same version, same
        capability scope, same canonical input. A key that names a different
        request is a collision, not a cache hit.

        Neither resolution a caller might hope for is safe. Serving the recorded
        result answers a question nobody asked. Executing the new request stores
        unaudited work under a key that already belongs to something else — and
        cannot even be written, since ``agent_tool_invocations`` is unique on
        ``(attempt_id, idempotency_key)``. So it raises.

        The input digest is recomputed from the stored redacted input rather
        than read from a column, so this stays correct without depending on the
        persistence layer to carry a digest alongside it.
        """

        mismatches: list[str] = []
        if recorded.tool_name != spec.name:
            mismatches.append(f"tool_name {recorded.tool_name!r} != {spec.name!r}")
        if recorded.tool_version != spec.version:
            mismatches.append(
                f"tool_version {recorded.tool_version!r} != {spec.version!r}"
            )
        if recorded.capability_scope != capability_scope:
            mismatches.append(
                f"capability_scope {recorded.capability_scope!r} != "
                f"{capability_scope!r}"
            )
        if boundary_digest(recorded.input) != input_sha256:
            mismatches.append("input differs from the recorded request")

        if mismatches:
            raise IdempotencyConflictError(
                f"idempotency key {recorded.idempotency_key!r} already identifies "
                f"a different request ({'; '.join(mismatches)}); a key names one "
                "request's content, not a slot that can be repointed at other work"
            )

    def _replay(
        self, recorded: ToolInvocation, *, decision: CapabilityDecision
    ) -> ToolOutcome:
        """Return the recorded outcome of an already-completed call.

        The decision is the one just made for *this* caller, not the one the
        original call was authorized under. A replayed result is not a bearer
        token: a caller whose grant has since been revoked never reaches here,
        and the outcome names the authorization that permitted this delivery.
        """

        status = _outcome_status_of(recorded)
        return ToolOutcome(
            mint=_OUTCOME_MINT,
            status=status,
            invocation=recorded,
            retry=RETRY_DISPOSITIONS[status],
            decision=decision,
            detail=recorded.status_reason,
        )

    @staticmethod
    def _in_progress(
        invocation: ToolInvocation, *, decision: CapabilityDecision
    ) -> ToolOutcome:
        """Return a retryable view of a committed nonterminal invocation."""

        return ToolOutcome(
            mint=_OUTCOME_MINT,
            status=ToolOutcomeStatus.IN_PROGRESS,
            invocation=invocation,
            retry=RetryDisposition.RETRIABLE,
            decision=decision,
            detail="invocation is already in progress; retry later",
        )


def _outcome_status_of(recorded: ToolInvocation) -> ToolOutcomeStatus:
    """Recover the finer outcome state from a durable record."""

    direct = {
        ToolInvocationStatus.SUCCEEDED: ToolOutcomeStatus.SUCCEEDED,
        ToolInvocationStatus.DENIED: ToolOutcomeStatus.DENIED,
        ToolInvocationStatus.TIMED_OUT: ToolOutcomeStatus.TIMED_OUT,
        ToolInvocationStatus.CANCELLED: ToolOutcomeStatus.CANCELLED,
    }.get(recorded.status)
    if direct is not None:
        return direct
    return _FAILED_STATUS_BY_ERROR_CODE.get(
        recorded.error_code or "", ToolOutcomeStatus.FAILED
    )


def _lease_duration(timeout_seconds: float) -> timedelta:
    """Give a lease the tool deadline plus a bounded safety grace period."""

    grace_seconds = max(
        _LEASE_GRACE_MIN_SECONDS,
        timeout_seconds * _LEASE_GRACE_RATIO,
    )
    return timedelta(seconds=timeout_seconds + grace_seconds)


async def _abandon(task: asyncio.Future[Any]) -> None:
    """Cancel a task and give cooperative cleanup a bounded grace period.

    Python cannot forcibly stop a coroutine that suppresses cancellation. An
    unbounded await here would therefore let one cancellation-resistant tool
    hold the boundary open forever, preventing its durable terminal state and
    lease recovery. After the grace period the task is quarantined: its result
    or exception is consumed when it eventually finishes, while the boundary's
    terminal record and the durable lease fence prevent that late result from
    becoming an outcome.
    """

    if task.done():
        if not task.cancelled():
            with suppress(BaseException):
                task.exception()
        return

    task.cancel()
    done, _pending = await asyncio.wait(
        (task,), timeout=_HANDLER_CANCELLATION_GRACE_SECONDS
    )
    if done:
        with suppress(BaseException):
            task.exception()
        return

    task.add_done_callback(_consume_abandoned_task)


def _consume_abandoned_task(task: asyncio.Future[Any]) -> None:
    """Consume a quarantined task's eventual result or exception."""

    if task.cancelled():
        return
    with suppress(BaseException):
        task.exception()


def _json_ready(value: Any) -> Any:
    """Serialize for the record, keeping a ``SecretRef`` in its handle form.

    ``model_dump`` is not usable here. A ``SecretRef`` is a ``ContractModel``,
    so pydantic dumps it as ``{"schema_version": ..., "secret_id": ...}`` —
    while ``redact`` recognizes a handle only in the ``{"$secret": ...}`` shape
    that :meth:`SecretRef.as_json` produces. The mismatch is not cosmetic:
    ``redact`` falls through to its credential-key rule and replaces the whole
    handle with the marker, destroying the record of *which* credential an
    invocation used while removing nothing sensitive — exactly the outcome the
    redaction contract's member rule was written to avoid.
    """

    if isinstance(value, SecretRef):
        return dict(value.as_json())
    if isinstance(value, BaseModel):
        return {
            name: _json_ready(getattr(value, name)) for name in type(value).model_fields
        }
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    return to_jsonable_python(value)


_JSON_OBJECT: Final[TypeAdapter[Mapping[str, JsonValue]]] = TypeAdapter(JsonObject)


def _ensure_representable(payload: Mapping[str, JsonValue]) -> None:
    """Run the contract's own validation of a record's ``input`` field.

    Called where the input is the suspect rather than several steps later where
    the record happens to be built, so a payload the contract cannot accept is
    reported as a bad input instead of surfacing as an unhandled error from an
    unrelated-looking line.
    """

    _JSON_OBJECT.validate_python(payload)


def _unprocessable_input() -> dict[str, JsonValue]:
    """The stand-in stored when the real input cannot be recorded.

    Flat by construction: it is written on the path that failed because a
    structure was too deep to walk, so it must not be walkable-deep itself.
    """

    return {"unprocessable_input": True}


def _redact_object(
    payload: Mapping[str, Any], secret_values: frozenset[str]
) -> Mapping[str, JsonValue]:
    return cast(
        Mapping[str, JsonValue],
        redact(_json_ready(dict(payload)), known_secret_values=secret_values),
    )


def _redact_text(value: str | None, secret_values: frozenset[str]) -> str | None:
    if value is None:
        return None
    return cast(str, redact(value, known_secret_values=secret_values))


def _uuid4_hex() -> str:
    return uuid.uuid4().hex


__all__ = ["CancellationToken", "Clock", "IdFactory", "ToolBoundary"]
