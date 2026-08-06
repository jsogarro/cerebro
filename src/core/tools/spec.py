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

The lint has one more hole worth naming, and it is closed here too: the check
walks ``model_fields``, so a schema that *admits fields it never declared* —
``extra="allow"`` — evades it entirely. A credential then arrives by not being
declared. Because the boundary also serializes a validated model over
``model_fields``, such a field reaches a handler while being absent from the
record, from ``input_sha256``, and from the approval binding ``input_sha256``
carries. :func:`_reject_undeclared_field_admission` refuses that shape.

Three smaller requirements are enforced here for the same reason — an omission
should not silently become a default:

- Every tool declares a **timeout**. There is no default deadline, because a
  default deadline is one nobody chose and nobody will tune.
- Every tool declares a **sensitivity**. It is what the capability layer
  authorizes against, and guessing it would guess at whether a call can reach
  the world outside the run.
- Every tool declares **its whole input schema**. A field the schema does not
  name is a field nothing downstream can audit.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, final, get_args

from pydantic import BaseModel

from src.core.contracts.capabilities import SensitivityClass
from src.core.contracts.redaction import SecretRef

from .errors import ToolSpecError
from .outcome import RetryDisposition

try:
    from src.core.contracts.redaction import (  # type: ignore[attr-defined]
        is_credential_key,
    )
except ImportError:  # pragma: no cover - whichever branch this tree is on
    # The contract fix that makes this public renames the private name outright
    # rather than aliasing it, so *neither* spelling works on both branches.
    # This bridge exists because the failure it prevents is a silent one: the
    # rename lives in a file this package does not touch, so integrating the two
    # branches produces no merge conflict — just an ImportError at runtime, in a
    # module whose tests would no longer import either.
    #
    # Collapse to the public import once the contract change is integrated.
    from src.core.contracts.redaction import (  # type: ignore[attr-defined,no-redef]
        _is_credential_key as is_credential_key,
    )

_is_credential_name: Final = is_credential_key
"""Reuse redaction's own key-name test rather than restating the list.

Two copies of a security-relevant word list drift, and the copy that drifts is
the one nobody is looking at.
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


def _reject_undeclared_field_admission(
    model: type[BaseModel], *, tool_name: str, seen: set[type[BaseModel]], path: str
) -> None:
    """Reject an input schema that admits fields it does not declare.

    The credential rule above is enforced over ``model_fields``, and so is the
    boundary's serialization of a validated model. A model configured
    ``extra="allow"`` accepts a field that appears in neither: it lands in
    ``model_extra``, which means it is absent from ``redacted_input``, from
    ``input_sha256``, from the approval binding ``input_sha256`` carries, and
    from the derived idempotency key — while a handler can still read it and
    forward it outbound. The audit record then denies material the call
    carried, and two materially different requests share a digest.

    So the credential rule is evadable by *omission*: declare no field named
    ``api_key`` and one arrives anyway, past a check that only inspects what
    was declared. This closes that, at the same registration-time point and
    for the same reason as the timeout and the sensitivity — an omission must
    not silently become a permission.

    ``extra="ignore"``, which is what a model gets by omission, is still
    accepted. Under ``ignore`` an undeclared field cannot enter the validated
    object at all, so nothing a handler can reach is missing from the record.
    What ``ignore`` does not cover is the caller's *submission*, which is a
    stated non-guarantee of this layer rather than a hole in it; requiring
    ``forbid`` everywhere would close that too, and is a larger change than
    this one.
    """

    if model in seen:
        return
    seen.add(model)

    if model.model_config.get("extra") == "allow":
        where = f" at {path!r}" if path else ""
        raise ToolSpecError(
            f"tool {tool_name!r} declares input schema {model.__name__!r}"
            f"{where} with extra='allow', so it admits undeclared fields. "
            "The boundary records, hashes, and fingerprints a validated model "
            "over its declared fields, so an undeclared field reaches a "
            "handler while being invisible to the record, to input_sha256, "
            "and to the approval that input_sha256 binds. Declare the field, "
            "or close the model."
        )

    for field_name, field in model.model_fields.items():
        nested_path = f"{path}.{field_name}" if path else field_name
        for nested in _model_types(field.annotation):
            _reject_undeclared_field_admission(
                nested, tool_name=tool_name, seen=seen, path=nested_path
            )


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
        _reject_undeclared_field_admission(
            self.input_model, tool_name=self.name, seen=set(), path=""
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
