"""Focused tests for the research kernel and its typed registry."""

from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from src.api.services.direct_execution_service import DirectExecutionService
from src.core.kernel import (
    DuplicateRegistryKeyError,
    RegistryEntry,
    RegistryKey,
    RegistryNamespace,
    ResearchKernel,
    TypedRegistry,
    UnknownRegistryKeyError,
)


def _entry(
    namespace: RegistryNamespace,
    name: str,
    component: object,
) -> RegistryEntry[object]:
    return RegistryEntry(
        key=RegistryKey(namespace=namespace, name=name),
        component=component,
    )


def test_registry_key_rejects_noncanonical_names() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"Registry key name must match "
            r"'\^\[a-z\]\[a-z0-9\]\*\(\?:\[._-\]\[a-z0-9\]\+\)\*\$'; "
            r"got 'Research Supervisor'"
        ),
    ):
        RegistryKey(
            namespace=RegistryNamespace.SUPERVISOR,
            name="Research Supervisor",
        )


def test_registry_key_rejects_invalid_field_types() -> None:
    with pytest.raises(
        TypeError,
        match=r"^Registry key namespace must be a RegistryNamespace; got str$",
    ):
        RegistryKey("supervisor", "research")  # type: ignore[arg-type]

    with pytest.raises(
        TypeError,
        match=r"^Registry key name must be a string; got int$",
    ):
        RegistryKey(RegistryNamespace.SUPERVISOR, 42)  # type: ignore[arg-type]


def test_registry_rejects_duplicate_keys_deterministically() -> None:
    first = _entry(RegistryNamespace.SUPERVISOR, "research", object())
    duplicate = _entry(RegistryNamespace.SUPERVISOR, "research", object())

    with pytest.raises(
        DuplicateRegistryKeyError,
        match=r"^Duplicate registry key: supervisor:research$",
    ):
        TypedRegistry([first, duplicate])


def test_registry_rejects_entry_with_invalid_key() -> None:
    invalid_entry = RegistryEntry(
        key="supervisor:research",  # type: ignore[arg-type]
        component=object(),
    )

    with pytest.raises(
        TypeError,
        match=r"^Registry entry key must be a RegistryKey; got str$",
    ):
        TypedRegistry([invalid_entry])


def test_registry_reports_unknown_key_with_sorted_available_keys() -> None:
    registry = TypedRegistry(
        [
            _entry(RegistryNamespace.SUPERVISOR, "research", object()),
            _entry(RegistryNamespace.AGENT, "citation", object()),
        ]
    )

    with pytest.raises(
        UnknownRegistryKeyError,
        match=(
            r"^Unknown registry key: workflow:comparative; "
            r"available keys: agent:citation, supervisor:research$"
        ),
    ):
        registry.resolve(
            RegistryKey(
                namespace=RegistryNamespace.WORKFLOW,
                name="comparative",
            )
        )


def test_registry_and_entries_are_immutable() -> None:
    component = object()
    entry = _entry(RegistryNamespace.SUPERVISOR, "research", component)
    registry = TypedRegistry([entry])

    assert registry.entries == (entry,)
    assert registry.resolve(entry.key) is component

    with pytest.raises(FrozenInstanceError):
        entry.component = object()  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        registry._entries = {}  # type: ignore[misc]


def test_registry_rejects_invalid_lookup_objects_deterministically() -> None:
    registry: TypedRegistry = TypedRegistry()

    with pytest.raises(
        TypeError,
        match=r"^Registry lookup requires a RegistryKey; got str$",
    ):
        registry.resolve("supervisor:research")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_kernel_owns_dependencies_and_preserves_executor_call_shape() -> None:
    registry = TypedRegistry(
        [_entry(RegistryNamespace.WORKFLOW, "comparative", object())]
    )
    calls: list[tuple[str, dict[str, object], bool]] = []

    async def execute(
        request: str,
        context: dict[str, object],
        *,
        fixture_mode: bool,
    ) -> str:
        calls.append((request, context, fixture_mode))
        return "run-123"

    kernel = ResearchKernel(executor=execute, registry=registry)

    result = await kernel.execute(
        "compare approaches",
        {"tenant": "tenant-1"},
        fixture_mode=True,
    )

    assert result == "run-123"
    assert calls == [
        ("compare approaches", {"tenant": "tenant-1"}, True),
    ]
    assert kernel.executor is execute
    assert kernel.registry is registry


def test_direct_execution_owns_default_typed_supervisor_catalog() -> None:
    service = DirectExecutionService(
        masr_router=Mock(),
        supervisor_bridge=Mock(),
        supervisor_factory=Mock(),
    )

    assert service.supervisor_registry.keys == (
        RegistryKey(RegistryNamespace.SUPERVISOR, "analytics"),
        RegistryKey(RegistryNamespace.SUPERVISOR, "content"),
        RegistryKey(RegistryNamespace.SUPERVISOR, "finance"),
        RegistryKey(RegistryNamespace.SUPERVISOR, "research"),
    )


def test_direct_execution_rejects_registry_without_research_fallback() -> None:
    with pytest.raises(
        UnknownRegistryKeyError,
        match=r"^Unknown registry key: supervisor:research; available keys: <none>$",
    ):
        DirectExecutionService(
            masr_router=Mock(),
            supervisor_bridge=Mock(),
            supervisor_factory=Mock(),
            supervisor_registry=TypedRegistry(),
        )


def test_direct_execution_rejects_invalid_supervisor_component() -> None:
    registry = TypedRegistry(
        [
            _entry(
                RegistryNamespace.SUPERVISOR,
                "research",
                object(),
            )
        ]
    )

    with pytest.raises(
        TypeError,
        match=(
            r"^Registry component supervisor:research must be a "
            r"BaseSupervisor subclass; got object$"
        ),
    ):
        DirectExecutionService(
            masr_router=Mock(),
            supervisor_bridge=Mock(),
            supervisor_factory=Mock(),
            supervisor_registry=registry,
        )
