"""Trusted lookup of immutable execution authority bindings."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)


class ExecutionAuthorityError(ValueError):
    """A fail-closed authority rejection suitable for an adapter to map to 422."""

    code: str


class ExecutionAuthorityRequiredError(ExecutionAuthorityError):
    """Raised when execution is requested without an authority reference."""

    code = "EXECUTION_AUTHORITY_REQUIRED"


class ExecutionAuthorityUnavailableError(ExecutionAuthorityError):
    """Raised when no trusted resolver can supply the referenced authority."""

    code = "EXECUTION_AUTHORITY_UNAVAILABLE"


class ExecutionAuthorityResolver(Protocol):
    """Resolves only a reference supplied by an execution adapter."""

    def resolve(
        self, reference: ExecutionAuthorityReference
    ) -> ExecutionAuthorityBinding:
        """Return the complete binding for one exact immutable reference."""


class MappingExecutionAuthorityResolver:
    """Immutable in-process resolver used by explicit trusted composition."""

    def __init__(
        self,
        bindings: Mapping[tuple[str, str], ExecutionAuthorityBinding],
    ) -> None:
        copied_bindings = dict(bindings)
        for (authority_id, authority_version), binding in copied_bindings.items():
            if (
                binding.authority_id != authority_id
                or binding.authority_version != authority_version
            ):
                raise ValueError("authority binding key must match binding identity")
        self._bindings: Mapping[tuple[str, str], ExecutionAuthorityBinding] = (
            MappingProxyType(copied_bindings)
        )

    def resolve(
        self, reference: ExecutionAuthorityReference
    ) -> ExecutionAuthorityBinding:
        try:
            return self._bindings[(reference.authority_id, reference.authority_version)]
        except KeyError as exc:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            ) from exc
