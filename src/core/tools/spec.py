"""What a tool must declare before the boundary will run it.

Packet 4A's non-guarantee 2: pattern redaction misses novel formats and
prose-embedded secrets, and only the ``SecretRef`` barrier is structural —
"nothing forces a tool author to use it". 4A's proposed fix was typed tool I/O
in which credential parameters are ``SecretRef``-typed, so a literal cannot
type-check.

This module implements that fix and closes the gap 4A named in the same breath:
that layer 1 "holds only where a tool adapter actually uses it, which the
contract layer cannot force". Registration can force it. A specification whose
input schema declares a credential-named field as anything other than a
``SecretRef`` is **rejected at registration** — not warned about, not scrubbed
at call time by a pattern that might miss it. The check recurses through nested
models and containers, because a credential one level down is still a
credential.

**This is not the Wave 4 non-goal wearing a disguise**, and the distinction is
worth stating because it looks like one at a glance. The non-goal rules out
prompt wording, classifiers, and allowlists as *standalone authorization or
security boundaries*. A name-shaped heuristic used at **runtime to decide
whether to permit a call** would be exactly that. This runs at **registration**
and decides nothing about any request: it forces a stronger *type*, and the
type is what carries the guarantee. It is a lint over the structural fix, not a
substitute for one.

Its honest limit: it only recognizes credential parameters whose names are
credential-shaped. ``upstream_credential`` is caught; ``pat``,
``bearer_material``, and anything a team invents are not. This is a ratchet,
not a guarantee — it converts "authors must remember" into "authors must
remember less often."

Two smaller requirements are enforced here for the same reason — an omission
should not silently become a default:

- Every tool declares a **timeout**. There is no default deadline, because a
  default deadline is one nobody chose and nobody will tune.
- Every tool declares a **sensitivity**. It is what the capability layer
  authorizes against, and guessing it would guess at whether a call can reach
  the world outside the run.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, final, get_args

from pydantic import BaseModel

from src.core.contracts.capabilities import SensitivityClass
from src.core.contracts.redaction import SecretRef, _is_credential_key

from .errors import ToolSpecError
from .outcome import RetryDisposition

_is_credential_name: Final = _is_credential_key
"""Reuse redaction's own key-name test rather than restating the list.

Deliberately importing a private name. Two copies of a security-relevant word
list drift, and the copy that drifts is the one nobody is looking at. If this
ever needs to be public, that is a contract change for 4A to make, not a second
list for this module to own.
"""


@final
@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """What a tool handler is given besides its validated arguments.

    ``resolve_secret`` is the only way a handler obtains credential material,
    and it is called *inside* the handler — downstream of validation, hashing,
    redaction, persistence, and publication. The value therefore exists only
    for the duration of the outbound call and never enters a record.
    """

    run_id: str
    task_id: str
    attempt_id: str
    tool_invocation_id: str
    resolve_secret: Callable[[SecretRef], str]


ToolHandler = Callable[[Any, ToolCallContext], Awaitable[Mapping[str, Any]]]


def _model_types(annotation: object) -> tuple[type[BaseModel], ...]:
    """Return every pydantic model reachable from an annotation."""

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    found: list[type[BaseModel]] = []
    for arg in get_args(annotation):
        found.extend(_model_types(arg))
    return tuple(found)


def _is_secret_ref_only(annotation: object) -> bool:
    """Return whether every leaf of ``annotation`` is ``SecretRef`` or ``None``.

    Accepts the forms a credential legitimately takes — ``SecretRef``,
    ``SecretRef | None``, ``list[SecretRef]`` — and nothing that could also
    hold a literal. ``str | SecretRef`` is rejected: a union that admits a
    string is a union through which a string arrives.
    """

    if annotation is SecretRef or annotation is type(None):
        return True
    args = tuple(arg for arg in get_args(annotation) if arg is not Ellipsis)
    if not args:
        return False
    return all(_is_secret_ref_only(arg) for arg in args)


def _audit_credential_fields(
    model: type[BaseModel], *, tool_name: str, seen: set[type[BaseModel]], path: str
) -> None:
    """Reject any credential-named field that is not ``SecretRef``-typed."""

    if model in seen:
        return
    seen.add(model)

    for field_name, field in model.model_fields.items():
        where = f"{path}.{field_name}" if path else field_name
        annotation = field.annotation
        if _is_credential_name(field_name) and not _is_secret_ref_only(annotation):
            raise ToolSpecError(
                f"tool {tool_name!r} declares credential field {where!r} as "
                f"{annotation!r}. A credential parameter must be typed "
                "SecretRef so a literal cannot type-check; pattern redaction "
                "is a defense layer, not a substitute for the barrier."
            )
        for nested in _model_types(annotation):
            _audit_credential_fields(nested, tool_name=tool_name, seen=seen, path=where)


@final
@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool's declared, enforceable contract with the boundary."""

    name: str
    version: str
    sensitivity: SensitivityClass
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    timeout_seconds: float
    handler: ToolHandler
    classify_error: Callable[[BaseException], RetryDisposition] | None = None
    """Optional per-tool retry classification.

    Absent, an unclassified error is ``TERMINAL``. Defaulting the unknown to
    retriable is how a call with side effects gets duplicated, so a tool that
    knows an error is transient has to say so.
    """

    def __post_init__(self) -> None:
        if not self.name:
            raise ToolSpecError("a tool specification names its tool")
        if not self.version:
            raise ToolSpecError(f"tool {self.name!r} declares no version")
        if self.timeout_seconds <= 0:
            raise ToolSpecError(
                f"tool {self.name!r} declares timeout_seconds="
                f"{self.timeout_seconds!r}; every tool needs a deadline "
                "somebody chose"
            )
        _audit_credential_fields(
            self.input_model, tool_name=self.name, seen=set(), path=""
        )

    def disposition_for(self, error: BaseException) -> RetryDisposition:
        """Classify a handler error, defaulting to ``TERMINAL``."""

        if self.classify_error is None:
            return RetryDisposition.TERMINAL
        return self.classify_error(error)


__all__ = ["ToolCallContext", "ToolHandler", "ToolSpec"]
