"""Focused tests for the research kernel and its typed registry."""

from dataclasses import FrozenInstanceError
from typing import cast
from unittest.mock import Mock

import pytest

from src.agents.base import BaseAgent
from src.agents.factory import AgentFactory
from src.agents.models import AgentResult, AgentTask
from src.ai_brain.providers.model_router import ModelRouter
from src.api.services.agent_execution_service import AgentExecutionService
from src.api.services.component_catalog import build_application_component_registry
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.research_kernel import compose_application_research_kernel
from src.core.kernel import (
    DuplicateRegistryKeyError,
    RegistryEntry,
    RegistryKey,
    RegistryNamespace,
    ResearchKernel,
    TypedRegistry,
    UnknownRegistryKeyError,
)
from src.core.kernel.component_keys import SUPERVISOR_KEYS
from src.models.agent_api_models import AgentType


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


def test_registry_rejects_forged_same_name_typed_token() -> None:
    registered_key = RegistryKey[object](
        RegistryNamespace.WORKFLOW,
        "routed-research",
    )
    registry = TypedRegistry([RegistryEntry(registered_key, object())])
    forged_key = cast(
        RegistryKey[object],
        RegistryKey[str](
            RegistryNamespace.WORKFLOW,
            "routed-research",
        ),
    )

    with pytest.raises(
        UnknownRegistryKeyError,
        match=(
            r"^Unknown registry key: workflow:routed-research; "
            r"available keys: workflow:routed-research$"
        ),
    ):
        registry.resolve(forged_key)


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

    assert tuple(
        key
        for key in service.supervisor_registry.keys
        if key.namespace is RegistryNamespace.SUPERVISOR
    ) == (
        SUPERVISOR_KEYS["analytics"],
        SUPERVISOR_KEYS["content"],
        SUPERVISOR_KEYS["finance"],
        SUPERVISOR_KEYS["research"],
    )


def test_application_kernel_registry_covers_active_component_catalogs() -> None:
    direct_service = DirectExecutionService(
        masr_router=Mock(),
        supervisor_bridge=None,
        supervisor_factory=None,
    )
    agent_service = AgentExecutionService()
    kernel = compose_application_research_kernel(direct_service, agent_service)

    qualified_names = tuple(key.qualified_name for key in kernel.registry.keys)

    assert qualified_names == (
        "agent:citation",
        "agent:comparative-analysis",
        "agent:content-planning",
        "agent:data-analysis",
        "agent:drafting",
        "agent:editing",
        "agent:financial-analysis",
        "agent:financial-calculator",
        "agent:insight-synthesis",
        "agent:literature-review",
        "agent:methodology",
        "agent:optimization",
        "agent:risk-assessment",
        "agent:statistical-modeling",
        "agent:synthesis",
        "agent:valuation",
        "agent:verification",
        "domain:analytics",
        "domain:content",
        "domain:finance",
        "domain:general",
        "domain:multimodal",
        "domain:research",
        "domain:service",
        "provider:deepseek",
        "provider:gemini",
        "provider:llama",
        "provider:openrouter",
        "supervisor:analytics",
        "supervisor:content",
        "supervisor:finance",
        "supervisor:research",
        "workflow:agent-chain",
        "workflow:agent-mixture",
        "workflow:collaboration-mode",
        "workflow:direct-agent",
        "workflow:routed-research",
    )
    assert direct_service.component_registry is kernel.registry
    assert direct_service.supervisor_registry is kernel.registry
    assert agent_service.component_registry is kernel.registry
    assert kernel.executor.component_registry is kernel.registry
    assert direct_service.supervisor_bridge.translator.component_registry is (
        kernel.registry
    )
    assert direct_service.supervisor_factory.component_registry is kernel.registry
    assert agent_service.agent_factory.component_registry is kernel.registry
    assert not hasattr(AgentFactory, "_agent_registry")
    assert "agent_type_mapping" not in agent_service.__dict__
    assert not hasattr(
        direct_service.supervisor_bridge.translator,
        "domain_to_supervisor",
    )
    assert not hasattr(
        direct_service.supervisor_bridge.translator,
        "collaboration_to_execution",
    )

    model_router = ModelRouter({"providers": {}}, component_registry=kernel.registry)
    assert model_router.component_registry is kernel.registry
    assert not hasattr(model_router, "provider_classes")


def test_application_kernel_rejects_split_registry_authority() -> None:
    direct_service = DirectExecutionService(
        masr_router=Mock(),
        supervisor_bridge=None,
        supervisor_factory=None,
    )
    agent_service = AgentExecutionService(
        component_registry=build_application_component_registry()
    )

    with pytest.raises(
        TypeError,
        match=(
            r"^Research and agent execution backends must share one "
            r"component registry$"
        ),
    ):
        compose_application_research_kernel(direct_service, agent_service)


@pytest.mark.asyncio
async def test_agent_service_resolves_agent_class_from_kernel_registry() -> None:
    class ReplacementAgent(BaseAgent):
        async def execute(self, task: AgentTask) -> AgentResult:
            raise NotImplementedError

        async def validate_result(self, result: AgentResult) -> bool:
            return True

        def get_agent_type(self) -> str:
            return "literature_review"

    direct_service = DirectExecutionService(
        masr_router=Mock(),
        supervisor_bridge=None,
        supervisor_factory=None,
    )
    entries = [
        RegistryEntry(entry.key, ReplacementAgent)
        if entry.key.qualified_name == "agent:literature-review"
        else entry
        for entry in direct_service.component_registry.entries
    ]
    registry = TypedRegistry(entries)
    service = AgentExecutionService(component_registry=registry)

    agent = await service._get_agent_instance(AgentType.LITERATURE_REVIEW)

    assert type(agent) is ReplacementAgent


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
    registry = TypedRegistry([RegistryEntry(SUPERVISOR_KEYS["research"], object())])

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
