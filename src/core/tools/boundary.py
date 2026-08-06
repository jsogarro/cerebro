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
  :class:`~src.core.tools.prompts.RenderedPrompt`. There is no parameter
  through which a bare digest can be supplied.
- Credential parameters are ``SecretRef``-typed, checked when the tool is
  registered rather than when it is called.
- ``now`` is read from the injected clock exactly once per call and is not a
  parameter of any public method, so a caller cannot supply a stale instant and
  revive an expired approval.
- The persisted ``capability_scope`` is read off the grant the decision named,
  never off the caller's request.

**Fail closed, and never into a value.** A denial, a timeout, an open breaker,
and a tool that raised are four distinct outcomes, none of which carries a
result body. Where the boundary cannot form a defensible answer at all — a
decision naming a grant nobody supplied — it raises rather than returning
anything a caller could mistake for one.
"""

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final, cast, final

from pydantic import BaseModel, JsonValue, ValidationError
from pydantic_core import to_jsonable_python

from src.core.contracts.capabilities import (
    ApprovalRef,
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityGrant,
    CapabilityRequest,
    decide_capability,
)
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
    ToolAuditEvent,
    ToolAuditStore,
    ToolEventPublisher,
)
from .circuit import BreakerRegistry
from .errors import (
    CapabilityDecisionUnusableError,
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
from .prompts import RenderedPrompt, require_rendered_prompt
from .secrets import SecretProvider
from .spec import ToolCallContext, ToolSpec

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
CapabilityDecider = Callable[..., CapabilityDecision]

_PRODUCER: Final[str] = "tool_boundary"

_TERMINAL_RECORD_STATUSES: Final[frozenset[ToolInvocationStatus]] = frozenset(
    {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
        ToolInvocationStatus.DENIED,
    }
)

_FAILED_STATUS_BY_ERROR_CODE: Final[dict[str, ToolOutcomeStatus]] = {
    ERROR_CODES[status]: status
    for status in (
        ToolOutcomeStatus.INVALID_INPUT,
        ToolOutcomeStatus.INVALID_OUTPUT,
        ToolOutcomeStatus.CIRCUIT_OPEN,
        ToolOutcomeStatus.FAILED,
    )
}


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
        verified_prompt = None if prompt is None else require_rendered_prompt(prompt)
        secret_values = self._secret_provider.secret_values()

        try:
            validated_input = spec.input_model.model_validate(dict(arguments))
        except ValidationError as error:
            return await self._reject_input(
                spec=spec,
                arguments=arguments,
                error=error,
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                organization_id=organization_id,
                capability_scope=capability_scope,
                idempotency_key=idempotency_key,
                input_trust=input_trust,
                prompt=verified_prompt,
                now=now,
                secret_values=secret_values,
            )

        redacted_input = _redact_object(_json_ready(validated_input), secret_values)
        input_sha256 = boundary_digest(redacted_input)
        effective_key = idempotency_key or self._derive_idempotency_key(
            spec=spec,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            capability_scope=capability_scope,
            input_sha256=input_sha256,
        )

        replayed = await self._audit_store.find_invocation(
            run_id=run_id,
            organization_id=organization_id,
            idempotency_key=effective_key,
        )
        if replayed is not None and replayed.status in _TERMINAL_RECORD_STATUSES:
            return self._replay(replayed)

        call = _Call(
            spec=spec,
            tool_invocation_id=self._id_factory(),
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            capability_scope=capability_scope,
            idempotency_key=effective_key,
            redacted_input=redacted_input,
            input_trust=input_trust,
            producer_kind=(
                ProducerKind.SYSTEM
                if verified_prompt is None
                else ProducerKind.MODEL_TURN
            ),
            prompt=verified_prompt,
            requested_at=now,
            secret_values=secret_values,
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
                input_sha256=input_sha256,
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

        breaker = self._breakers.for_tool(spec.name)
        if not breaker.allows(now):
            return await self._terminate(
                call,
                status=ToolOutcomeStatus.CIRCUIT_OPEN,
                completed_at=now,
                decision=decision,
                detail=f"circuit open for {spec.name!r}",
            )

        await self._record(
            call,
            invocation=ToolInvocation(
                tool_invocation_id=call.tool_invocation_id,
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                tool_name=spec.name,
                tool_version=spec.version,
                status=ToolInvocationStatus.REQUESTED,
                capability_scope=call.capability_scope,
                idempotency_key=effective_key,
                input=redacted_input,
                input_trust=input_trust,
                producer_kind=call.producer_kind,
                prompt_binding=(
                    None if verified_prompt is None else verified_prompt.binding
                ),
                requested_at=now,
            ),
            event_type=EVENT_REQUESTED,
            payload={
                "tool_name": spec.name,
                "tool_version": spec.version,
                "capability_scope": call.capability_scope,
                "grant_id": grant.grant_id,
                "input_sha256": input_sha256,
                "input_trust": input_trust.value,
            },
        )

        return await self._run_tool(
            call,
            validated_input=validated_input,
            declared_output_trust=output_trust,
            decision=decision,
            cancellation=cancellation,
        )

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
                spec, validated_input, context, cancellation
            )
        except asyncio.CancelledError:
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

        if status is not ToolOutcomeStatus.CANCELLED:
            # A withdrawn request says nothing about the dependency's health,
            # so cancellation alone must not push the breaker toward opening.
            breaker.record_failure(completed_at)

        return await self._terminate(
            call,
            status=status,
            completed_at=completed_at,
            decision=decision,
            detail=detail,
            retry=(
                spec.disposition_for(error)
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

        handler_task: asyncio.Task[Mapping[str, Any]] = asyncio.ensure_future(
            spec.handler(validated_input, context)
        )
        watchers: list[asyncio.Future[Any]] = [handler_task]
        cancel_task: asyncio.Task[None] | None = None
        if cancellation is not None:
            cancel_task = asyncio.ensure_future(cancellation.wait())
            watchers.append(cancel_task)

        try:
            done, _pending = await asyncio.wait(
                watchers,
                timeout=spec.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if cancel_task is not None and not cancel_task.done():
                await _abandon(cancel_task)

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

    # ------------------------------------------------------------------
    # recording
    # ------------------------------------------------------------------

    async def _reject_input(
        self,
        *,
        spec: ToolSpec,
        arguments: Mapping[str, Any],
        error: ValidationError,
        run_id: str,
        task_id: str,
        attempt_id: str,
        organization_id: str | None,
        capability_scope: str,
        idempotency_key: str | None,
        input_trust: TrustClassification,
        prompt: RenderedPrompt | None,
        now: datetime,
        secret_values: frozenset[str],
    ) -> ToolOutcome:
        """Record an invocation whose arguments never passed the input schema.

        Recorded rather than raised. A rejected call is a real thing that
        happened to a run, and an audit trail with a hole where every malformed
        request should be is exactly the trail an attacker would prefer.
        """

        redacted = _redact_object(dict(arguments), secret_values)
        call = _Call(
            spec=spec,
            tool_invocation_id=self._id_factory(),
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            capability_scope=capability_scope,
            idempotency_key=idempotency_key
            or self._derive_idempotency_key(
                spec=spec,
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                capability_scope=capability_scope,
                input_sha256=boundary_digest(redacted),
            ),
            redacted_input=redacted,
            input_trust=input_trust,
            producer_kind=(
                ProducerKind.SYSTEM if prompt is None else ProducerKind.MODEL_TURN
            ),
            prompt=prompt,
            requested_at=now,
            secret_values=secret_values,
        )
        return await self._terminate(
            call,
            status=ToolOutcomeStatus.INVALID_INPUT,
            completed_at=now,
            detail=_redact_text(str(error), secret_values),
        )

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
        await self._record(
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
        )
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
        )
        await self._event_publisher.publish((event,))

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

    def _replay(self, recorded: ToolInvocation) -> ToolOutcome:
        """Return the recorded outcome of an already-completed call."""

        status = _outcome_status_of(recorded)
        return ToolOutcome(
            mint=_OUTCOME_MINT,
            status=status,
            invocation=recorded,
            retry=RETRY_DISPOSITIONS[status],
            decision=None,
            detail=recorded.status_reason,
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


async def _abandon(task: asyncio.Future[Any]) -> None:
    """Cancel a task that lost its race and wait for it to actually stop.

    Waiting matters: returning while the handler is still running would let a
    timed-out call keep its side effects in flight after the boundary has
    already recorded it as timed out.
    """

    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


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
