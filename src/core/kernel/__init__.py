"""Canonical internal execution kernel boundary."""

from .bounded_concurrency import BoundedTaskRunner
from .registry import (
    DuplicateRegistryKeyError,
    RegistryEntry,
    RegistryKey,
    RegistryNamespace,
    TypedRegistry,
    UnknownRegistryKeyError,
)
from .research_kernel import KernelExecutor, ResearchKernel

__all__ = [
    "BoundedTaskRunner",
    "DuplicateRegistryKeyError",
    "KernelExecutor",
    "RegistryEntry",
    "RegistryKey",
    "RegistryNamespace",
    "ResearchKernel",
    "TypedRegistry",
    "UnknownRegistryKeyError",
]
