"""Typed, immutable registry primitives for kernel-owned components."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast

ComponentT = TypeVar("ComponentT")
ResolvedComponentT = TypeVar("ResolvedComponentT")

_REGISTRY_NAME_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
_REGISTRY_NAME_RE = re.compile(_REGISTRY_NAME_PATTERN)


class RegistryNamespace(StrEnum):
    """Kinds of implementation component owned by the kernel registry."""

    AGENT = "agent"
    DOMAIN = "domain"
    PROVIDER = "provider"
    SUPERVISOR = "supervisor"
    WORKFLOW = "workflow"


@dataclass(frozen=True, slots=True)
class RegistryKey(Generic[ComponentT]):
    """Validated, namespaced identity for one registered component."""

    namespace: RegistryNamespace
    name: str
    _identity: object = field(default_factory=object, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, RegistryNamespace):
            raise TypeError(
                "Registry key namespace must be a RegistryNamespace; "
                f"got {type(self.namespace).__name__}"
            )
        if not isinstance(self.name, str):
            raise TypeError(
                f"Registry key name must be a string; got {type(self.name).__name__}"
            )
        if not _REGISTRY_NAME_RE.fullmatch(self.name):
            raise ValueError(
                f"Registry key name must match {_REGISTRY_NAME_PATTERN!r}; "
                f"got {self.name!r}"
            )

    @property
    def qualified_name(self) -> str:
        """Return the deterministic external form of this key."""

        return f"{self.namespace.value}:{self.name}"


@dataclass(frozen=True, slots=True)
class RegistryEntry(Generic[ComponentT]):
    """Immutable descriptor pairing a validated key with a typed component."""

    key: RegistryKey[ComponentT]
    component: ComponentT


class DuplicateRegistryKeyError(ValueError):
    """Raised when registry construction receives the same key twice."""


class UnknownRegistryKeyError(LookupError):
    """Raised when a registry lookup cannot resolve a key."""


@dataclass(frozen=True, slots=True, init=False)
class TypedRegistry:
    """Immutable collection of typed component descriptors."""

    _entries: Mapping[RegistryKey[Any], RegistryEntry[Any]]

    def __init__(
        self,
        entries: Iterable[RegistryEntry[Any]] = (),
    ) -> None:
        indexed: dict[RegistryKey[Any], RegistryEntry[Any]] = {}
        qualified_names: set[str] = set()
        for entry in entries:
            if not isinstance(entry, RegistryEntry):
                raise TypeError(
                    "Registry construction requires RegistryEntry values; "
                    f"got {type(entry).__name__}"
                )
            if not isinstance(entry.key, RegistryKey):
                raise TypeError(
                    "Registry entry key must be a RegistryKey; "
                    f"got {type(entry.key).__name__}"
                )
            if entry.key.qualified_name in qualified_names:
                raise DuplicateRegistryKeyError(
                    f"Duplicate registry key: {entry.key.qualified_name}"
                )
            indexed[entry.key] = entry
            qualified_names.add(entry.key.qualified_name)
        object.__setattr__(self, "_entries", MappingProxyType(indexed))

    @property
    def keys(self) -> tuple[RegistryKey[Any], ...]:
        """Return keys in deterministic qualified-name order."""

        return tuple(
            sorted(self._entries, key=lambda key: key.qualified_name),
        )

    @property
    def entries(self) -> tuple[RegistryEntry[Any], ...]:
        """Return immutable descriptors in deterministic key order."""

        return tuple(self._entries[key] for key in self.keys)

    def resolve(self, key: RegistryKey[ResolvedComponentT]) -> ResolvedComponentT:
        """Resolve a component or raise a deterministic unknown-key error."""

        if not isinstance(key, RegistryKey):
            raise TypeError(
                f"Registry lookup requires a RegistryKey; got {type(key).__name__}"
            )
        entry = self._entries.get(key)
        if entry is not None:
            return cast(ResolvedComponentT, entry.component)

        available = ", ".join(item.qualified_name for item in self.keys) or "<none>"
        raise UnknownRegistryKeyError(
            f"Unknown registry key: {key.qualified_name}; available keys: {available}"
        )

    def __len__(self) -> int:
        return len(self._entries)
