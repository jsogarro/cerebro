"""Exceptions raised *by* the boundary, as distinct from outcomes it returns.

The distinction is deliberate and is the whole reason this module is separate.

An **outcome** is what happened to a tool call that the boundary agreed to
mediate: it succeeded, it was denied, it timed out, the breaker was open. Every
one of those is a normal, expected, durably-recorded branch of execution, and
every one of them is returned as a value so a caller cannot accidentally ignore
it.

An **exception** here means the boundary was asked to do something it cannot do
safely: a tool whose specification is unsound, a prompt binding that does not
describe the text it accompanies, an authorization decision that names a grant
nobody supplied. None of those can be turned into a result without inventing
one, so none of them is returned as a result. They fail loudly and closed.
"""

from src.core.contracts.provenance import ToolInvocation


class ToolBoundaryError(Exception):
    """Base class for every failure the boundary refuses to paper over."""


class ToolSpecError(ToolBoundaryError):
    """A tool's declared specification is unsound and cannot be registered.

    Raised at registration rather than at call time on purpose: an unsound
    specification is an authoring mistake, and finding it the first time the
    tool is actually used means finding it in production.
    """


class ToolNotRegisteredError(ToolBoundaryError):
    """A caller asked for a tool the boundary does not know about."""


class PromptBindingRefusedError(ToolBoundaryError):
    """A prompt binding does not correspond to the material it claims to pin.

    This is the enforcement of packet 4A's non-guarantee 1. The contract layer
    can require that a digest is *present* and well-formed; only something
    holding both the digest and the text can require that they *correspond*.
    """


class IdempotencyConflictError(ToolBoundaryError):
    """An idempotency key was presented for a request it does not identify.

    A key asserts "this is the same call as before". When the recorded call
    under that key is demonstrably a *different* one — another tool, another
    version, another capability scope, or different input — the assertion is
    false and there is no safe way to proceed. Serving the recorded result
    answers a question nobody asked; executing the new request stores unaudited
    work under a key that already belongs to something else.

    It is also not recordable: ``agent_tool_invocations`` is unique on
    ``(attempt_id, idempotency_key)``, so there is no row a conflict could be
    written to. Hence an exception rather than a terminal outcome.
    """


class ToolInvocationConflictError(ToolBoundaryError):
    """A concurrent caller already reserved or completed this invocation.

    The durable store raises this only after the database's idempotency index
    has rejected a competing first write and the existing row has been
    reloaded. The boundary can then return the same safe result as a normal
    terminal or pending lookup, without publishing an event for the losing
    caller's unpersisted invocation.
    """

    def __init__(self, invocation: ToolInvocation) -> None:
        self.invocation = invocation
        super().__init__(
            "another caller already recorded this tool invocation under the "
            f"idempotency key {invocation.idempotency_key!r}"
        )


class UnknownSecretError(ToolBoundaryError):
    """A :class:`~src.core.contracts.redaction.SecretRef` names no held secret.

    Failing is the only safe response. Resolving to an empty string would send
    an unauthenticated request that a caller believes was authenticated.
    """


class CapabilityDecisionUnusableError(ToolBoundaryError):
    """An authorization decision cannot be acted on, so nothing is executed.

    An ``ALLOW`` naming a grant absent from the supplied set is the case this
    exists for. Executing anyway would mean running a tool under a permission
    the boundary cannot produce, and therefore cannot record or audit.
    """


class ToolOutcomeNotSuccessfulError(ToolBoundaryError):
    """A caller read the result of a call that did not produce one.

    The failure mode this closes is the one packet 4-Char characterized: a
    degraded or fabricated value handed to a user as though a tool had returned
    it. There is no value to read, so asking for one raises instead of
    returning a plausible substitute.
    """


__all__ = [
    "CapabilityDecisionUnusableError",
    "IdempotencyConflictError",
    "PromptBindingRefusedError",
    "ToolBoundaryError",
    "ToolInvocationConflictError",
    "ToolNotRegisteredError",
    "ToolOutcomeNotSuccessfulError",
    "ToolSpecError",
    "UnknownSecretError",
]
