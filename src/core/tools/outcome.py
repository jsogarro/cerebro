"""What a mediated tool call produces, and why it can never look like success.

Packet 4-Char characterized the failure this module is built against. The
existing integration computes ``data_source = "mcp_tools" if success else
"fallback"`` — it inspects only whether a call reported success, never whether
the value came from a tool at all — so a fabricated fallback reaches the user
labelled as a real tool result. The circuit breaker compounds it: one flaky
call and a sustained outage produce the identical success-shaped value, so a
caller cannot tell them apart.

Three properties here make that unrepresentable rather than discouraged.

**One success state, seven distinct terminal failure states, and one explicit
in-flight state.** Timeout, cancellation, open breaker, denial, invalid input,
invalid output, and tool error are separate values. A committed request that
has not terminated is not a failure and must not be treated as permission to
execute the same idempotency key again, so it has its own ``IN_PROGRESS``
state.

**No failure carries a body.** A non-success outcome has no output, at the type
level and in the persisted record. A fallback therefore cannot smuggle a
fabricated value through a failure branch; there is nowhere to put it.

**Only the boundary can mint one.** ``ToolOutcome`` refuses construction
without the boundary's token. Code downstream of the boundary — including the
migration in packet 4D — cannot manufacture a successful outcome for a call
that never happened.
"""

from enum import StrEnum
from typing import Final, final

from src.core.contracts.capabilities import CapabilityDecision
from src.core.contracts.provenance import ToolInvocation, ToolInvocationStatus

from .errors import ToolBoundaryError, ToolOutcomeNotSuccessfulError

_MINT: Final = object()
"""The boundary's construction token. Deliberately module-private."""


class ToolOutcomeStatus(StrEnum):
    """Every way a mediated call can be observed. Exactly one is success."""

    SUCCEEDED = "succeeded"

    IN_PROGRESS = "in_progress"
    """The request is durably admitted and has not reached a terminal state.

    This is returned for an idempotent retry while the original request may
    still be running. It carries no output or error because neither has been
    established yet, and it is explicitly retryable without re-executing the
    handler in the current call.
    """

    DENIED = "denied"
    """No capability grant authorized the call. The tool was never reached."""

    INVALID_INPUT = "invalid_input"
    """The arguments failed the tool's declared input schema."""

    INVALID_OUTPUT = "invalid_output"
    """The tool returned something its declared output schema rejects.

    Distinct from ``FAILED`` because it means the *tool* is wrong — a schema
    drift or a poisoned response — not that the call failed. Retrying cannot
    help, and passing the value through would let unvalidated content into the
    system under a validated tool's name.
    """

    TIMED_OUT = "timed_out"
    """The call exceeded the tool's declared deadline."""

    CANCELLED = "cancelled"
    """The caller withdrew the request before it completed."""

    CIRCUIT_OPEN = "circuit_open"
    """The breaker was open, so the call was never attempted.

    Kept distinct from ``FAILED`` because that difference is the operational
    signal: one failure is a flaky call, an open breaker is a sustained outage.
    4-Char found both rendered identically today.
    """

    FAILED = "failed"
    """The tool raised."""


class RetryDisposition(StrEnum):
    """Whether re-attempting this call could plausibly change the answer."""

    RETRIABLE = "retriable"
    TERMINAL = "terminal"


SUCCESS_STATUS: Final = ToolOutcomeStatus.SUCCEEDED

_INVOCATION_STATUS: Final[dict[ToolOutcomeStatus, ToolInvocationStatus]] = {
    ToolOutcomeStatus.SUCCEEDED: ToolInvocationStatus.SUCCEEDED,
    ToolOutcomeStatus.IN_PROGRESS: ToolInvocationStatus.REQUESTED,
    ToolOutcomeStatus.DENIED: ToolInvocationStatus.DENIED,
    ToolOutcomeStatus.INVALID_INPUT: ToolInvocationStatus.FAILED,
    ToolOutcomeStatus.INVALID_OUTPUT: ToolInvocationStatus.FAILED,
    ToolOutcomeStatus.TIMED_OUT: ToolInvocationStatus.TIMED_OUT,
    ToolOutcomeStatus.CANCELLED: ToolInvocationStatus.CANCELLED,
    ToolOutcomeStatus.CIRCUIT_OPEN: ToolInvocationStatus.FAILED,
    ToolOutcomeStatus.FAILED: ToolInvocationStatus.FAILED,
}
"""How each outcome state is written to the frozen ``ToolInvocation`` enum.

Three outcome states share ``FAILED`` because the contract enum has no name for
them. That is a lossy projection, so it is compensated rather than accepted:
:data:`ERROR_CODES` gives each of them a distinct, queryable ``error_code``, and
the outcome the caller holds keeps the finer state. A future contract revision
could widen the enum; until then the durable record still distinguishes an open
breaker from a tool that raised.
"""

ERROR_CODES: Final[dict[ToolOutcomeStatus, str]] = {
    ToolOutcomeStatus.DENIED: "capability_denied",
    ToolOutcomeStatus.INVALID_INPUT: "invalid_input",
    ToolOutcomeStatus.INVALID_OUTPUT: "invalid_output",
    ToolOutcomeStatus.TIMED_OUT: "timed_out",
    ToolOutcomeStatus.CANCELLED: "cancelled",
    ToolOutcomeStatus.CIRCUIT_OPEN: "circuit_open",
    ToolOutcomeStatus.FAILED: "tool_error",
}

RETRY_DISPOSITIONS: Final[dict[ToolOutcomeStatus, RetryDisposition]] = {
    ToolOutcomeStatus.SUCCEEDED: RetryDisposition.TERMINAL,
    ToolOutcomeStatus.IN_PROGRESS: RetryDisposition.RETRIABLE,
    # A denial is a decision, not a transient condition. Re-asking the same
    # question gets the same answer; what changes it is a new grant.
    ToolOutcomeStatus.DENIED: RetryDisposition.TERMINAL,
    ToolOutcomeStatus.INVALID_INPUT: RetryDisposition.TERMINAL,
    ToolOutcomeStatus.INVALID_OUTPUT: RetryDisposition.TERMINAL,
    ToolOutcomeStatus.TIMED_OUT: RetryDisposition.RETRIABLE,
    ToolOutcomeStatus.CANCELLED: RetryDisposition.TERMINAL,
    ToolOutcomeStatus.CIRCUIT_OPEN: RetryDisposition.RETRIABLE,
    # An unclassified tool error is terminal by default. Classifying the
    # unknown as retriable is how a call with side effects gets duplicated, so
    # a tool that knows better says so through its specification's classifier.
    ToolOutcomeStatus.FAILED: RetryDisposition.TERMINAL,
}


def invocation_status_for(status: ToolOutcomeStatus) -> ToolInvocationStatus:
    """Return the contract status that records ``status`` durably."""

    return _INVOCATION_STATUS[status]


@final
class ToolOutcome:
    """The single value a mediated tool call returns.

    Construction requires the boundary's token, so a successful outcome cannot
    be manufactured by any caller for a call that did not happen.
    """

    __slots__ = ("_decision", "_detail", "_invocation", "_retry", "_status")

    def __init__(
        self,
        *,
        mint: object,
        status: ToolOutcomeStatus,
        invocation: ToolInvocation,
        retry: RetryDisposition,
        decision: CapabilityDecision | None = None,
        detail: str | None = None,
    ) -> None:
        if mint is not _MINT:
            raise ToolBoundaryError(
                "a ToolOutcome is minted by the tool boundary; constructing "
                "one elsewhere would let a fabricated value be presented as a "
                "tool result"
            )
        self._validate(status, invocation)
        self._status = status
        self._invocation = invocation
        self._retry = retry
        self._decision = decision
        self._detail = detail

    @staticmethod
    def _validate(status: ToolOutcomeStatus, invocation: ToolInvocation) -> None:
        expected = invocation_status_for(status)
        if status is ToolOutcomeStatus.IN_PROGRESS:
            valid_in_progress_statuses = {
                ToolInvocationStatus.REQUESTED,
                ToolInvocationStatus.RUNNING,
            }
            if invocation.status not in valid_in_progress_statuses:
                raise ToolBoundaryError(
                    f"outcome {status.value!r} must be recorded as a "
                    "nonterminal invocation, not "
                    f"{invocation.status.value!r}"
                )
        elif invocation.status is not expected:
            raise ToolBoundaryError(
                f"outcome {status.value!r} must be recorded as "
                f"{expected.value!r}, not {invocation.status.value!r}"
            )
        if status is ToolOutcomeStatus.SUCCEEDED:
            if invocation.output is None:
                raise ToolBoundaryError("a successful outcome carries its output")
            if invocation.error_code is not None:
                raise ToolBoundaryError("a successful outcome carries no error code")
            return
        if status is ToolOutcomeStatus.IN_PROGRESS:
            if invocation.output is not None:
                raise ToolBoundaryError(
                    "an in-progress outcome carries no output before completion"
                )
            if invocation.error_code is not None:
                raise ToolBoundaryError(
                    "an in-progress outcome carries no terminal error code"
                )
            return
        if invocation.output is not None:
            raise ToolBoundaryError(
                f"a {status.value!r} outcome carries no output; a failure with "
                "a body is how a fabricated result reaches a caller"
            )
        if invocation.error_code is None:
            raise ToolBoundaryError(f"a {status.value!r} outcome names its error")

    @property
    def status(self) -> ToolOutcomeStatus:
        return self._status

    @property
    def succeeded(self) -> bool:
        return self._status is ToolOutcomeStatus.SUCCEEDED

    @property
    def invocation(self) -> ToolInvocation:
        """The durable record of this call, including its provenance."""

        return self._invocation

    @property
    def retry(self) -> RetryDisposition:
        return self._retry

    @property
    def decision(self) -> CapabilityDecision | None:
        """The authorization decision, present whenever one was reached."""

        return self._decision

    @property
    def error_code(self) -> str | None:
        return self._invocation.error_code

    @property
    def detail(self) -> str | None:
        """Operator-facing description. Redacted, like everything persisted."""

        return self._detail

    def unwrap(self) -> object:
        """Return the tool's output, or raise if there is none.

        There is no accessor that returns ``None`` on failure. A caller that
        forgets to check the status gets an exception rather than an empty
        result it can mistake for an answer.
        """

        if self._status is not ToolOutcomeStatus.SUCCEEDED:
            raise ToolOutcomeNotSuccessfulError(
                f"call ended {self._status.value!r} "
                f"({self._invocation.error_code}); there is no result to read"
            )
        return self._invocation.output

    def __repr__(self) -> str:
        return (
            f"ToolOutcome(status={self._status.value!r}, "
            f"tool={self._invocation.tool_name!r}, "
            f"error_code={self._invocation.error_code!r})"
        )


__all__ = [
    "ERROR_CODES",
    "RETRY_DISPOSITIONS",
    "RetryDisposition",
    "ToolOutcome",
    "ToolOutcomeStatus",
    "invocation_status_for",
]
