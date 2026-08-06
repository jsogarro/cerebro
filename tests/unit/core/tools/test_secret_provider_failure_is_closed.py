"""A provider that cannot answer must stop the call, not empty the defence.

Packet 4A named this as an addition to its non-guarantee 5. The provider is
queried on every redaction rather than snapshotted at construction, so that a
credential rotated or lazily loaded after start-up is covered. That choice has
a cost: the provider is now on the hot path of every boundary crossing, and its
failure modes become the boundary's.

Two of them matter, and both have the same shape — the shape of non-guarantee 5
itself, where a security layer is lost by omission while everything still looks
green:

*The store errors.* Proceeding with an empty set would silently disable
exact-value redaction on exactly the call where the secret store was
unreachable. An exception is the correct outcome.

*The store holds a value too short to redact.* ``redact`` raises on anything
under ``MIN_REDACTABLE_SECRET_LENGTH``, so a single short value in the store
does not fail once at boot — it fails *every tool call*, at redaction time, far
from the misconfiguration. ``validate_secret_provider`` exists to move that to
start-up.
"""

from typing import Any

import pytest

from src.core.tools import (
    MappingSecretProvider,
    NullSecretProvider,
    ToolBoundary,
    validate_secret_provider,
)

from .conftest import RecordingAuditStore, RecordingPublisher, invoke_kwargs


class BrokenProvider:
    """A secret store that is unreachable, as one eventually is."""

    def secret_values(self) -> frozenset[str]:
        raise ConnectionError("the secret store is unreachable")

    def resolve(self, secret_id: str) -> str:
        raise ConnectionError("the secret store is unreachable")


class ShortSecretProvider:
    """A store holding a value too short to match by substring."""

    def secret_values(self) -> frozenset[str]:
        return frozenset({"abc"})

    def resolve(self, secret_id: str) -> str:
        return "abc"


class TestAProviderFailureStopsTheCall:
    async def test_the_tool_is_never_reached(
        self, boundary_dependencies: dict[str, Any], echo_spec: Any
    ) -> None:
        boundary_dependencies["secret_provider"] = BrokenProvider()
        built = ToolBoundary(**boundary_dependencies)
        built.register(echo_spec)

        with pytest.raises(ConnectionError):
            await built.invoke(**invoke_kwargs())

    async def test_nothing_is_recorded_or_published(
        self,
        boundary_dependencies: dict[str, Any],
        echo_spec: Any,
        audit_store: RecordingAuditStore,
        publisher: RecordingPublisher,
    ) -> None:
        """Failing closed means no half-finished record either."""

        boundary_dependencies["secret_provider"] = BrokenProvider()
        built = ToolBoundary(**boundary_dependencies)
        built.register(echo_spec)

        with pytest.raises(ConnectionError):
            await built.invoke(**invoke_kwargs())

        assert audit_store.invocations == []
        assert publisher.published == []

    async def test_a_short_held_value_fails_rather_than_being_ignored(
        self, boundary_dependencies: dict[str, Any], echo_spec: Any
    ) -> None:
        """``redact`` refuses; the boundary must not swallow that refusal."""

        boundary_dependencies["secret_provider"] = ShortSecretProvider()
        built = ToolBoundary(**boundary_dependencies)
        built.register(echo_spec)

        with pytest.raises(ValueError, match="too short to redact"):
            await built.invoke(**invoke_kwargs())


class TestBootTimeValidation:
    def test_a_short_held_value_is_a_startup_error(self) -> None:
        with pytest.raises(ValueError, match="shorter than"):
            validate_secret_provider(ShortSecretProvider())

    def test_a_healthy_provider_passes(self) -> None:
        validate_secret_provider(MappingSecretProvider({"k": "long-enough-value"}))

    def test_holding_nothing_passes(self) -> None:
        validate_secret_provider(NullSecretProvider())

    def test_an_unreachable_store_is_not_mistaken_for_an_empty_one(self) -> None:
        """The whole point: "cannot be queried" is not "holds nothing"."""

        with pytest.raises(ConnectionError):
            validate_secret_provider(BrokenProvider())
