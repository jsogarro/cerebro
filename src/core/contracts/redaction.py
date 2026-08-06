"""The redaction contract, and its interaction with content hashing.

**Where.** Redaction is a boundary responsibility, not a call-site one. Every
payload crossing the tool-execution boundary — a tool input, a tool output, an
event payload, an artifact body destined for a prompt — passes through
:func:`redact` in exactly one place. A call site that redacts is a call site
that can forget to; the boundary cannot be bypassed by adding a new tool.

**What — and which notion of "secret".** "Secret" means two different things,
and this contract answers to both, in three layers of decreasing strength. A
reviewer must be able to tell which failure mode each layer accepts, so each
is stated.

*Layer 1 — the boundary: :class:`SecretRef` indirection.* A credential appears
in a tool input as a **handle**, never as a literal. The boundary resolves the
handle to a value inside the tool adapter, downstream of everything that is
hashed, persisted, published, or placed in a prompt. Because the credential is
never in the payload, no classifier has to catch it. *Accepted failure mode:*
none from matching — this layer cannot mismatch. Its weakness is coverage: it
holds only where a tool adapter actually uses it, which the contract layer
cannot force.

*Layer 2 — exact value: ``known_secret_values``.* Values this system actually
holds, matched **exactly** as substrings. Deterministic, with **no false
positives** by construction, and it catches secrets in formats no pattern
knows. *Accepted failure mode:* **under-redaction** — it cannot remove a secret
the system never held in a known variable, such as a third party's key pasted
into a scraped page. Values shorter than
:data:`MIN_REDACTABLE_SECRET_LENGTH` are rejected rather than applied, because
substring-matching a short value would scrub unrelated content.

*Layer 3 — shape: credential key names and value patterns.* The safety net for
secrets arriving from **outside** — a scraped page containing an API key, a
tool echoing an Authorization header. *Accepted failure mode:*
**over-redaction**. This layer removes anything credential-*shaped*, including
content that is not a credential: AWS's reserved documentation placeholder
``AKIAIOSFODNN7EXAMPLE`` is scrubbed even though it is a published example. It
will also miss novel formats, so it under-redacts too.

**We deliberately accept layer 3's over-redaction**, and the reason is
structural rather than a judgement call: redaction never touches snapshot
bytes. A false positive can therefore degrade a prompt or an event excerpt, but
it can never corrupt the evidentiary record, change what a source is recorded
as having said, or shift a byte offset a locator depends on. The cost is
bounded to non-evidentiary surfaces; the cost of the opposite choice — a leaked
credential — is not bounded at all. The residual cost that *is* real: a
persisted ``ToolInvocation.output`` is stored in redacted form, so an
over-redacted tool result is lossy in the audit record. The unredacted source
of truth remains the snapshot artifact.

Per the Wave 4 non-goal, layers 2 and 3 are defenses, **not** an authorization
or security boundary. Layer 1 is the boundary.

**Hashing.** The two digest functions are deliberately not interchangeable,
because they answer different questions:

:func:`boundary_digest`
    Over the **redacted** canonical JSON of a boundary record. Everything
    persisted, published, or hashed at the boundary is the redacted form, so a
    stored record's digest is reproducible from the record as stored. Hashing
    raw and storing redacted would make the digest permanently uncheckable —
    worse than no digest, because it looks like integrity.

:func:`snapshot_digest`
    Over the **verbatim acquired bytes** of an immutable source snapshot,
    unredacted. Evidence integrity means "this is what the source said";
    scrubbing the snapshot would change that, and would shift every byte offset
    a locator depends on.

The rule that reconciles them: **redact after locate, never before.** A
locator resolves against unredacted snapshot bytes so its offsets stay stable;
redaction is applied to the extracted excerpt, on the way into a prompt, an
event, or a user-visible artifact. The snapshot store is therefore a
quarantined zone: unredacted at rest, redacted on every read that leaves it.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import JsonValue

from .base import ContentSha256, ContractId, ContractModel

REDACTION_MARKER: Final[str] = "[REDACTED]"
"""The single replacement token. Idempotence depends on it being recognizable."""

_CREDENTIAL_KEY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "passwd",
        "privatekey",
        "pwd",
        "refreshtoken",
        "secret",
        "secretkey",
        "sessionid",
        "setcookie",
        "signature",
        "token",
    }
)

_CREDENTIAL_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*KEY-----"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
)

_KEY_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]")


class SecretRef(ContractModel):
    """A credential referenced by handle rather than by value.

    This is the only legal way a credential appears in a tool input. It is
    safe to serialize, hash, persist, and show to a reviewer, because it
    carries no secret material — only the identity of one.
    """

    secret_id: ContractId

    def as_json(self) -> dict[str, JsonValue]:
        """Return the serialized form embedded in a tool input payload."""

        return {"$secret": self.secret_id}


def _normalize_key(key: str) -> str:
    return _KEY_SEPARATORS.sub("", key.lower())


def is_credential_key(key: str) -> bool:
    """Return whether ``key`` names a field that carries credential material.

    Public because the boundary needs the same judgement at tool-registration
    time — to reject a spec that declares a credential parameter as a bare
    ``str`` instead of a :class:`SecretRef`. A second copy of the word list
    would drift, and the drifting copy is the one nobody reads.

    Used that way this is a lint that forces a structural fix at authoring
    time, not a runtime authorization check. Per the Wave 4 non-goal, a
    name-shaped heuristic must never be what decides whether a call is
    permitted.
    """

    normalized = _normalize_key(key)
    if normalized in _CREDENTIAL_KEY_NAMES:
        return True
    # "x-api-key" normalizes to "xapikey"; strip a leading vendor prefix.
    return normalized.startswith("x") and normalized[1:] in _CREDENTIAL_KEY_NAMES


MIN_REDACTABLE_SECRET_LENGTH: Final[int] = 8
"""Shortest held value that may be matched by substring.

A two-character "secret" occurs inside ordinary prose, and redacting by
substring on it would scrub the whole corpus. Anything shorter than this is a
configuration error and is rejected rather than applied.
"""


def _redact_text(value: str, known_secret_values: frozenset[str]) -> str:
    if value == REDACTION_MARKER:
        return value
    redacted = value
    # Exact values first: they are certain, so removing them before the
    # heuristic pass keeps the heuristic from splitting a known secret into
    # fragments that no longer match it.
    for secret in known_secret_values:
        redacted = redacted.replace(secret, REDACTION_MARKER)
    for pattern in _CREDENTIAL_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTION_MARKER, redacted)
    return redacted


def _is_secret_ref(value: Mapping[str, object]) -> bool:
    """Recognize a secret handle in **either** serialization it can arrive as.

    ``as_json()`` produces the compact embedded form ``{"$secret": id}``.
    Pydantic's ``model_dump()`` — what a boundary reaches for when persisting a
    validated tool input — produces ``{"schema_version": ..., "secret_id":
    id}``. Recognizing only the first meant the second fell through to the
    credential-key rule and the whole handle was replaced, destroying which
    credential an invocation used while removing nothing sensitive.

    Both are accepted so no caller has to route through a particular method to
    keep provenance. Neither carries secret material, so widening recognition
    widens nothing that matters.
    """

    keys = set(value)
    if keys == {"$secret"}:
        return isinstance(value.get("$secret"), str)
    if "secret_id" in keys and keys <= {"schema_version", "secret_id"}:
        return isinstance(value.get("secret_id"), str)
    return False


def _redact_member(
    key: str, value: object, known_secret_values: frozenset[str]
) -> object:
    """Redact one mapping member, letting an explicit ``SecretRef`` survive.

    The reference check precedes the key-name check on purpose. A handle under
    a credential-named key — ``{"api_key": {"$secret": "..."}}`` — is the
    correct shape, and scrubbing it would destroy the provenance of *which*
    credential an invocation used while removing nothing sensitive.
    """

    if isinstance(value, Mapping) and _is_secret_ref(value):
        return dict(value)
    if is_credential_key(key):
        return REDACTION_MARKER
    return redact(value, known_secret_values=known_secret_values)


def redact(
    payload: object, *, known_secret_values: frozenset[str] = frozenset()
) -> object:
    """Return ``payload`` with credential material replaced, without mutation.

    Redaction is **idempotent**: ``redact(redact(x)) == redact(x)``. That is
    what lets a persisted record be re-redacted on read without changing its
    digest, and what lets a pipeline stage run twice without corrupting a
    record it already scrubbed.

    Args:
        payload: Any JSON-shaped value. Mappings, sequences, and strings are
            traversed; other scalars are returned unchanged.
        known_secret_values: Credential values this system actually holds. They
            are matched **exactly**, as substrings, so this layer has no false
            positives and catches secrets in formats no pattern knows. Callers
            supply them from the secret store; the contract layer never reads
            the environment itself.

    Returns:
        A new structure of plain ``dict``/``list``/scalars with credential
        material replaced by :data:`REDACTION_MARKER`.

    Raises:
        ValueError: A value in ``known_secret_values`` is shorter than
            :data:`MIN_REDACTABLE_SECRET_LENGTH`. Substring-matching on a short
            value would scrub unrelated content, so it fails loudly instead.
    """

    short = sorted(
        secret
        for secret in known_secret_values
        if len(secret) < MIN_REDACTABLE_SECRET_LENGTH
    )
    if short:
        raise ValueError(
            f"{len(short)} known secret value(s) are too short to redact by "
            f"value; minimum is {MIN_REDACTABLE_SECRET_LENGTH} characters"
        )

    if isinstance(payload, Mapping):
        if _is_secret_ref(payload):
            return dict(payload)
        return {
            str(key): _redact_member(str(key), item, known_secret_values)
            for key, item in payload.items()
        }
    if isinstance(payload, str):
        return _redact_text(payload, known_secret_values)
    if isinstance(payload, Sequence) and not isinstance(payload, bytes | bytearray):
        return [
            redact(item, known_secret_values=known_secret_values) for item in payload
        ]
    return payload


def _canonical_json(payload: Mapping[str, JsonValue]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )


def _json_default(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON-serializable")


def boundary_digest(payload: Mapping[str, JsonValue]) -> ContentSha256:
    """Hash a boundary record's canonical JSON.

    Callers must pass the **redacted** form. This function does not redact for
    them: a digest function that silently redacted would make it impossible to
    tell, from a call site, whether the value being hashed is the value being
    stored.

    Args:
        payload: The redacted boundary record.

    Returns:
        The lowercase hex SHA-256 of the canonical JSON serialization, which
        is independent of key order.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("boundary_digest expects a JSON object")
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def snapshot_digest(data: bytes) -> ContentSha256:
    """Hash the verbatim acquired bytes of an immutable source snapshot.

    Args:
        data: The bytes exactly as acquired, before any redaction. Redacting
            here would change what the source is recorded as having said and
            would invalidate every byte offset a locator depends on.

    Returns:
        The lowercase hex SHA-256 of ``data``.

    Raises:
        TypeError: ``data`` is not ``bytes``. The type check is the guard that
            keeps this from being used where :func:`boundary_digest` belongs.
    """

    if not isinstance(data, bytes | bytearray):
        raise TypeError("snapshot_digest expects the acquired bytes of a snapshot")
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "MIN_REDACTABLE_SECRET_LENGTH",
    "REDACTION_MARKER",
    "SecretRef",
    "boundary_digest",
    "is_credential_key",
    "redact",
    "snapshot_digest",
]
