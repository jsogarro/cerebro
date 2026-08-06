"""Held credential material, and the two questions the boundary asks about it.

Packet 4A left redaction pure: :func:`~src.core.contracts.redaction.redact`
takes the held values as an argument and defaults them to an empty set, so a
call site that forgets them loses the exact-value layer with no error, no log
line, and nothing visible in a review of the call. This module exists so that
losing it requires a deliberate act.

A provider answers two questions, and they are separate because they have
different blast radii:

``secret_values()``
    What must never appear in anything persisted, published, or put in a
    prompt. Queried at the moment of redaction rather than snapshotted when the
    boundary is built, so a credential loaded after start-up — a rotation, a
    lazily-read secret manager — is covered by the redaction that follows it. A
    snapshot would under-redact silently, which is the same failure this module
    was written to prevent.

``resolve()``
    What a :class:`~src.core.contracts.redaction.SecretRef` handle stands for.
    Called inside the tool adapter, downstream of everything hashed, persisted,
    or published, so the value exists only for the duration of the outbound
    call.
"""

from collections.abc import Mapping
from typing import Protocol, final, runtime_checkable

from src.core.contracts.redaction import MIN_REDACTABLE_SECRET_LENGTH

from .errors import UnknownSecretError


@runtime_checkable
class SecretProvider(Protocol):
    """The boundary's required source of credential material."""

    def secret_values(self) -> frozenset[str]:
        """Return every held value that must be scrubbed from boundary records.

        Called on each redaction, not cached, so late-loaded and rotated
        credentials are covered.
        """
        ...

    def resolve(self, secret_id: str) -> str:
        """Return the value behind a handle.

        Raises:
            UnknownSecretError: No secret is held under ``secret_id``. Failing
                is the only safe answer; an empty string would silently send an
                unauthenticated request.
        """
        ...


@final
class NullSecretProvider:
    """A provider for a deployment that holds no credentials.

    This is the point of the module. "No secrets" remains expressible — but as
    a named type at the construction site, where a reviewer sees it, rather
    than as an omitted argument that looks like every other omitted argument.
    """

    def secret_values(self) -> frozenset[str]:
        return frozenset()

    def resolve(self, secret_id: str) -> str:
        raise UnknownSecretError(
            f"no secret is held under {secret_id!r}; this deployment is "
            "configured with NullSecretProvider"
        )


@final
class MappingSecretProvider:
    """A provider over an in-memory mapping of handle to value.

    Values shorter than
    :data:`~src.core.contracts.redaction.MIN_REDACTABLE_SECRET_LENGTH` are
    rejected here rather than at redaction time. ``redact`` raises on them,
    which would turn a configuration mistake into a failure of the first tool
    call that happened to redact something — far from the cause.
    """

    def __init__(self, secrets: Mapping[str, str]) -> None:
        too_short = sorted(
            secret_id
            for secret_id, value in secrets.items()
            if len(value) < MIN_REDACTABLE_SECRET_LENGTH
        )
        if too_short:
            raise ValueError(
                f"secret(s) {too_short} are shorter than "
                f"{MIN_REDACTABLE_SECRET_LENGTH} characters and cannot be "
                "redacted by value"
            )
        self._secrets = dict(secrets)

    def secret_values(self) -> frozenset[str]:
        return frozenset(self._secrets.values())

    def resolve(self, secret_id: str) -> str:
        try:
            return self._secrets[secret_id]
        except KeyError:
            raise UnknownSecretError(f"no secret is held under {secret_id!r}") from None


def validate_secret_provider(provider: SecretProvider) -> None:
    """Check at start-up that a provider's contents can actually be redacted.

    Call this once at boot. ``redact`` raises on any held value shorter than
    :data:`~src.core.contracts.redaction.MIN_REDACTABLE_SECRET_LENGTH`, because
    substring-matching a short value would scrub ordinary prose. Since the
    boundary queries the provider on **every** redaction rather than
    snapshotting it, one short value in the store does not fail once — it fails
    every tool call, at redaction time, far from the misconfiguration that
    caused it.

    Surfacing it here turns that into a start-up configuration error, which is
    where it can be read and fixed.

    Raises:
        ValueError: A held value is too short to redact by substring.
        Exception: Whatever the provider raises. Propagating is deliberate — a
            provider that cannot be queried at boot must not be treated as a
            provider that holds nothing.
    """

    too_short = sorted(
        value
        for value in provider.secret_values()
        if len(value) < MIN_REDACTABLE_SECRET_LENGTH
    )
    if too_short:
        raise ValueError(
            f"{len(too_short)} held secret value(s) are shorter than "
            f"{MIN_REDACTABLE_SECRET_LENGTH} characters and cannot be redacted "
            "by value; every tool call would fail at redaction time"
        )


__all__ = [
    "MappingSecretProvider",
    "NullSecretProvider",
    "SecretProvider",
    "validate_secret_provider",
]
