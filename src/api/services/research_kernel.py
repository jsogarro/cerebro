"""Application composition for the canonical research execution kernel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol, TypeAlias, cast
from uuid import UUID

from fastapi import Depends, Request

from src.api.services.agent_execution_service import (
    get_application_agent_execution_service,
)
from src.api.services.direct_execution_service import (
    get_application_direct_execution_service,
)
from src.core.kernel import (
    ResearchKernel,
    TypedRegistry,
    UnknownRegistryKeyError,
)
from src.core.kernel.component_keys import (
    AGENT_CHAIN_WORKFLOW_KEY,
    AGENT_MIXTURE_WORKFLOW_KEY,
    DIRECT_AGENT_WORKFLOW_KEY,
    ROUTED_RESEARCH_WORKFLOW_KEY,
)
from src.models.agent_api_models import (
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentHealthStatus,
    AgentInfo,
    AgentMetricsResponse,
    AgentType,
    ChainOfAgentsRequest,
    ChainOfAgentsResponse,
    MixtureOfAgentsRequest,
    MixtureOfAgentsResponse,
)
from src.models.execution_authority import ExecutionAuthorityReference
from src.models.research_project import ResearchProject


class ResearchExecutionBackend(Protocol):
    """Legacy operations implemented by the direct-execution backend."""

    async def start_research_execution(
        self,
        project: ResearchProject,
        context: dict[str, Any] | None = None,
        *,
        authority_reference: ExecutionAuthorityReference | None = None,
    ) -> str: ...

    async def get_execution_status(self, execution_id: str) -> Any: ...

    async def get_execution_results(
        self, execution_id: str
    ) -> dict[str, Any] | None: ...

    async def resume_execution(self, project_id: UUID) -> str | None: ...


class AgentExecutionBackend(Protocol):
    """Legacy operations implemented by the direct-agent backend."""

    async def execute_single_agent(
        self,
        agent_type: AgentType,
        request: AgentExecutionRequest,
    ) -> AgentExecutionResponse: ...

    async def execute_chain_of_agents(
        self,
        request: ChainOfAgentsRequest,
    ) -> ChainOfAgentsResponse: ...

    async def execute_mixture_of_agents(
        self,
        request: MixtureOfAgentsRequest,
    ) -> MixtureOfAgentsResponse: ...

    async def get_agent_list(self) -> list[AgentInfo]: ...

    async def get_agent_metrics(
        self,
        agent_type: AgentType,
    ) -> AgentMetricsResponse: ...

    async def get_agent_health(
        self,
        agent_type: AgentType,
    ) -> AgentHealthStatus: ...

    async def get_service_stats(self) -> dict[str, Any]: ...

    async def get_active_execution_snapshot(
        self,
    ) -> tuple[dict[str, dict[str, Any]], int]: ...


class AgentKernelOperations(AgentExecutionBackend, Protocol):
    """Stateless agent calls exposed by the application kernel adapter."""


RoutedResearchWorkflow: TypeAlias = Callable[
    [
        ResearchExecutionBackend,
        ResearchProject,
        dict[str, Any] | None,
        ExecutionAuthorityReference | None,
    ],
    Awaitable[str],
]
DirectAgentWorkflow: TypeAlias = Callable[
    [AgentExecutionBackend, AgentType, AgentExecutionRequest],
    Awaitable[AgentExecutionResponse],
]
AgentChainWorkflow: TypeAlias = Callable[
    [AgentExecutionBackend, ChainOfAgentsRequest],
    Awaitable[ChainOfAgentsResponse],
]
AgentMixtureWorkflow: TypeAlias = Callable[
    [AgentExecutionBackend, MixtureOfAgentsRequest],
    Awaitable[MixtureOfAgentsResponse],
]


@dataclass(frozen=True, slots=True)
class _ApplicationKernelAdapter:
    """Stateless call-shape adapter over the existing execution backends."""

    backend: ResearchExecutionBackend
    component_registry: TypedRegistry
    agent_backend: AgentExecutionBackend | None = None

    async def __call__(
        self,
        project: ResearchProject,
        context: dict[str, Any] | None = None,
        *,
        authority_reference: ExecutionAuthorityReference | None = None,
    ) -> str:
        try:
            workflow = cast(
                RoutedResearchWorkflow,
                self.component_registry.resolve(ROUTED_RESEARCH_WORKFLOW_KEY),
            )
        except UnknownRegistryKeyError:
            if authority_reference is None:
                return await self.backend.start_research_execution(project, context)
            return await self.backend.start_research_execution(
                project,
                context,
                authority_reference=authority_reference,
            )
        return await workflow(self.backend, project, context, authority_reference)

    async def get_execution_status(self, execution_id: str) -> Any:
        return await self.backend.get_execution_status(execution_id)

    async def get_execution_results(
        self,
        execution_id: str,
    ) -> dict[str, Any] | None:
        return await self.backend.get_execution_results(execution_id)

    async def resume_execution(self, project_id: UUID) -> str | None:
        return await self.backend.resume_execution(project_id)

    def _agents(self) -> AgentExecutionBackend:
        if self.agent_backend is None:
            raise TypeError("Research kernel was not composed for agent execution")
        return self.agent_backend

    async def execute_single_agent(
        self,
        agent_type: AgentType,
        request: AgentExecutionRequest,
    ) -> AgentExecutionResponse:
        backend = self._agents()
        try:
            workflow = cast(
                DirectAgentWorkflow,
                self.component_registry.resolve(DIRECT_AGENT_WORKFLOW_KEY),
            )
        except UnknownRegistryKeyError:
            return await backend.execute_single_agent(agent_type, request)
        return await workflow(backend, agent_type, request)

    async def execute_chain_of_agents(
        self,
        request: ChainOfAgentsRequest,
    ) -> ChainOfAgentsResponse:
        backend = self._agents()
        try:
            workflow = cast(
                AgentChainWorkflow,
                self.component_registry.resolve(AGENT_CHAIN_WORKFLOW_KEY),
            )
        except UnknownRegistryKeyError:
            return await backend.execute_chain_of_agents(request)
        return await workflow(backend, request)

    async def execute_mixture_of_agents(
        self,
        request: MixtureOfAgentsRequest,
    ) -> MixtureOfAgentsResponse:
        backend = self._agents()
        try:
            workflow = cast(
                AgentMixtureWorkflow,
                self.component_registry.resolve(AGENT_MIXTURE_WORKFLOW_KEY),
            )
        except UnknownRegistryKeyError:
            return await backend.execute_mixture_of_agents(request)
        return await workflow(backend, request)

    async def get_agent_list(self) -> list[AgentInfo]:
        return await self._agents().get_agent_list()

    async def get_agent_metrics(
        self,
        agent_type: AgentType,
    ) -> AgentMetricsResponse:
        return await self._agents().get_agent_metrics(agent_type)

    async def get_agent_health(
        self,
        agent_type: AgentType,
    ) -> AgentHealthStatus:
        return await self._agents().get_agent_health(agent_type)

    async def get_service_stats(self) -> dict[str, Any]:
        return await self._agents().get_service_stats()

    async def get_active_execution_snapshot(
        self,
    ) -> tuple[dict[str, dict[str, Any]], int]:
        return await self._agents().get_active_execution_snapshot()


ApplicationResearchKernel: TypeAlias = ResearchKernel[..., str]


def compose_application_research_kernel(
    backend: ResearchExecutionBackend,
    agent_backend: AgentExecutionBackend | None = None,
) -> ApplicationResearchKernel:
    """Compose a canonical kernel over the existing execution implementation."""

    registry = getattr(backend, "component_registry", None)
    if not isinstance(registry, TypedRegistry):
        registry = getattr(backend, "supervisor_registry", None)
    if not isinstance(registry, TypedRegistry):
        # Lightweight compatibility fakes predate the typed registry. They remain
        # substitutable at the HTTP boundary without becoming production catalogs.
        registry = TypedRegistry()
    agent_registry = getattr(agent_backend, "component_registry", None)
    if isinstance(agent_registry, TypedRegistry) and agent_registry is not registry:
        raise TypeError(
            "Research and agent execution backends must share one component registry"
        )
    return ResearchKernel(
        executor=_ApplicationKernelAdapter(backend, registry, agent_backend),
        registry=registry,
    )


def _execution_adapter(
    kernel: ApplicationResearchKernel,
) -> _ApplicationKernelAdapter:
    """Return the adapter owned by a composed application kernel."""

    adapter = kernel.executor
    if not isinstance(adapter, _ApplicationKernelAdapter):
        raise TypeError("Research kernel was not composed for direct execution")
    return adapter


def get_application_research_kernel(
    request: Request,
    backend: Annotated[
        ResearchExecutionBackend,
        Depends(get_application_direct_execution_service),
    ],
) -> ApplicationResearchKernel:
    """Resolve the lifespan-owned kernel or adapt an overridden legacy backend."""

    kernel = getattr(request.app.state, "research_kernel", None)
    if isinstance(kernel, ResearchKernel):
        adapter = kernel.executor
        if (
            isinstance(adapter, _ApplicationKernelAdapter)
            and adapter.backend is backend
        ):
            return cast(ApplicationResearchKernel, kernel)

    # Existing test and integration clients override the established direct
    # execution dependency, including raw ASGI clients that do not run lifespan.
    return compose_application_research_kernel(backend)


def get_application_agent_research_kernel(
    request: Request,
    backend: Annotated[
        AgentExecutionBackend,
        Depends(get_application_agent_execution_service),
    ],
) -> ApplicationResearchKernel:
    """Resolve the app kernel or adapt an overridden agent backend."""

    kernel = getattr(request.app.state, "research_kernel", None)
    if isinstance(kernel, ResearchKernel):
        adapter = kernel.executor
        if isinstance(adapter, _ApplicationKernelAdapter):
            if adapter.agent_backend is backend:
                return cast(ApplicationResearchKernel, kernel)
            return compose_application_research_kernel(adapter.backend, backend)

    return get_legacy_agent_research_kernel(backend)


def get_legacy_research_kernel() -> ApplicationResearchKernel:
    """Retain direct-call compatibility outside FastAPI dependency injection."""

    from .direct_execution_service import get_direct_execution_service

    return compose_application_research_kernel(get_direct_execution_service())


def get_legacy_agent_research_kernel(
    backend: AgentExecutionBackend | None = None,
) -> ApplicationResearchKernel:
    """Retain direct-call and raw-ASGI compatibility for legacy agent clients."""

    from .agent_execution_service import get_agent_execution_service
    from .direct_execution_service import get_direct_execution_service

    return compose_application_research_kernel(
        get_direct_execution_service(),
        backend if backend is not None else get_agent_execution_service(),
    )


def get_kernel_agent_operations(
    kernel: ApplicationResearchKernel,
) -> AgentKernelOperations:
    """Return stateless agent methods owned by the canonical kernel adapter."""

    adapter = _execution_adapter(kernel)
    adapter._agents()
    return adapter


async def get_kernel_execution_status(
    kernel: ApplicationResearchKernel,
    execution_id: str,
) -> Any:
    """Read status through the adapter owned by the canonical kernel."""

    return await _execution_adapter(kernel).get_execution_status(execution_id)


async def get_kernel_execution_results(
    kernel: ApplicationResearchKernel,
    execution_id: str,
) -> dict[str, Any] | None:
    """Read results through the adapter owned by the canonical kernel."""

    return await _execution_adapter(kernel).get_execution_results(execution_id)


async def resume_kernel_execution(
    kernel: ApplicationResearchKernel,
    project_id: UUID,
) -> str | None:
    """Resume through the adapter owned by the canonical kernel."""

    return await _execution_adapter(kernel).resume_execution(project_id)


__all__ = [
    "AgentExecutionBackend",
    "AgentKernelOperations",
    "ApplicationResearchKernel",
    "ResearchExecutionBackend",
    "compose_application_research_kernel",
    "get_application_agent_research_kernel",
    "get_application_research_kernel",
    "get_kernel_agent_operations",
    "get_kernel_execution_results",
    "get_kernel_execution_status",
    "get_legacy_agent_research_kernel",
    "get_legacy_research_kernel",
    "resume_kernel_execution",
]
