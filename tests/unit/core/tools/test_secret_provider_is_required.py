"""The secret provider is a required dependency, not an optional argument.

Packet 4A reported that :func:`src.core.contracts.redaction.redact` is pure and
takes held secrets as an argument, so passing an empty set silently disables
its exact-value layer — a security guarantee lost by omission and invisible at
the call site. These tests pin the structural fix: the boundary cannot exist
without a provider, so holding no secrets has to be *chosen* rather than
defaulted into.
"""

import inspect

import pytest

from src.core.tools import NullSecretProvider, ToolBoundary, UnknownSecretError


class TestTheProviderCannotBeOmitted:
    def test_constructing_a_boundary_without_a_secret_provider_fails(
        self, boundary_dependencies: dict[str, object]
    ) -> None:
        del boundary_dependencies["secret_provider"]

        with pytest.raises(TypeError, match="secret_provider"):
            ToolBoundary(**boundary_dependencies)  # type: ignore[arg-type]

    def test_the_parameter_has_no_default(self) -> None:
        """A default would reintroduce exactly the failure mode 4A named."""

        parameter = inspect.signature(ToolBoundary.__init__).parameters[
            "secret_provider"
        ]

        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


class TestHoldingNoSecretsIsAnExplicitChoice:
    def test_the_null_provider_holds_nothing_and_says_so(self) -> None:
        provider = NullSecretProvider()

        assert provider.secret_values() == frozenset()
        with pytest.raises(UnknownSecretError):
            provider.resolve("anything")

    def test_a_boundary_can_be_built_with_it(
        self, boundary_dependencies: dict[str, object]
    ) -> None:
        boundary_dependencies["secret_provider"] = NullSecretProvider()

        assert ToolBoundary(**boundary_dependencies) is not None  # type: ignore[arg-type]
