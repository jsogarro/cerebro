"""The redaction contract, and its interaction with content hashing.

**Where.** Redaction is a boundary responsibility, not a call-site one. Every
payload crossing the tool-execution boundary — a tool input, a tool output, an
event payload, an artifact body destined for a prompt — passes through
:func:`redact` in exactly one place. A call site that redacts is a call site
that can forget to; the boundary cannot be bypassed by adding a new tool.

**What.** Two layers, and they are not equally strong:

*The boundary* is :class:`SecretRef` indirection. A credential appears in a
tool input as a **handle**, never as a literal. The boundary resolves the
handle to a value inside the tool adapter, downstream of everything that is
hashed, persisted, published, or placed in a prompt. Because the credential is
never in the payload, no classifier has to catch it.

*The safety net* is pattern-based scrubbing: a frozen set of credential key
names, and a frozen set of credential-shaped value patterns. This exists for
secrets that arrive from **outside** — a scraped page containing an API key, a
tool that echoes an Authorization header. Per the Wave 4 non-goal, this layer
is a defense, **not** an authorization or security boundary. It will miss
novel formats. It is not what keeps our own credentials out of prompts;
``SecretRef`` is.

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


def _is_credential_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _CREDENTIAL_KEY_NAMES:
        return True
    # "x-api-key" normalizes to "xapikey"; strip a leading vendor prefix.
    return normalized.startswith("x") and normalized[1:] in _CREDENTIAL_KEY_NAMES


def _redact_text(value: str) -> str:
    if value == REDACTION_MARKER:
        return value
    redacted = value
    for pattern in _CREDENTIAL_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTION_MARKER, redacted)
    return redacted


def _is_secret_ref(value: Mapping[str, object]) -> bool:
    return set(value) == {"$secret"} and isinstance(value.get("$secret"), str)


def _redact_member(key: str, value: object) -> object:
    """Redact one mapping member, letting an explicit ``SecretRef`` survive.

    The reference check precedes the key-name check on purpose. A handle under
    a credential-named key — ``{"api_key": {"$secret": "..."}}`` — is the
    correct shape, and scrubbing it would destroy the provenance of *which*
    credential an invocation used while removing nothing sensitive.
    """

    if isinstance(value, Mapping) and _is_secret_ref(value):
        return dict(value)
    if _is_credential_key(key):
        return REDACTION_MARKER
    return redact(value)


def redact(payload: object) -> object:
    """Return ``payload`` with credential material replaced, without mutation.

    Redaction is **idempotent**: ``redact(redact(x)) == redact(x)``. That is
    what lets a persisted record be re-redacted on read without changing its
    digest, and what lets a pipeline stage run twice without corrupting a
    record it already scrubbed.

    Args:
        payload: Any JSON-shaped value. Mappings, sequences, and strings are
            traversed; other scalars are returned unchanged.

    Returns:
        A new structure of plain ``dict``/``list``/scalars with credential
        material replaced by :data:`REDACTION_MARKER`.
    """

    if isinstance(payload, Mapping):
        if _is_secret_ref(payload):
            return dict(payload)
        return {
            str(key): _redact_member(str(key), item) for key, item in payload.items()
        }
    if isinstance(payload, str):
        return _redact_text(payload)
    if isinstance(payload, Sequence) and not isinstance(payload, bytes | bytearray):
        return [redact(item) for item in payload]
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
    "REDACTION_MARKER",
    "SecretRef",
    "boundary_digest",
    "redact",
    "snapshot_digest",
]
