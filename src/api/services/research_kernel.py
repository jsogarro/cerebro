"""Application composition for the canonical research execution kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Protocol, TypeAlias, cast
from uuid import UUID

from fastapi import Depends, Request

from src.api.services.direct_execution_service import (
    get_application_direct_execution_service,
)
from src.core.kernel import ResearchKernel, TypedRegistry
from src.models.research_project import ResearchProject


class ResearchExecutionBackend(Protocol):
    """Legacy operations implemented by the direct-execution backend."""

    async def start_research_execution(
        self,
        project: ResearchProject,
        context: dict[str, Any] | None = None,
    ) -> str: ...

    async def get_execution_status(self, execution_id: str) -> Any: ...

    async def get_execution_results(
        self, execution_id: str
    ) -> dict[str, Any] | None: ...

    async def resume_execution(self, project_id: UUID) -> str | None: ...


@dataclass(frozen=True, slots=True)
class _DirectExecutionKernelAdapter:
    """Stateless call-shape adapter over the single execution implementation."""

    backend: ResearchExecutionBackend

    async def __call__(
        self,
        project: ResearchProject,
        context: dict[str, Any] | None = None,
    ) -> str:
        return await self.backend.start_research_execution(project, context)

    async def get_execution_status(self, execution_id: str) -> Any:
        return await self.backend.get_execution_status(execution_id)

    async def get_execution_results(
        self,
        execution_id: str,
    ) -> dict[str, Any] | None:
        return await self.backend.get_execution_results(execution_id)

    async def resume_execution(self, project_id: UUID) -> str | None:
        return await self.backend.resume_execution(project_id)


ApplicationResearchKernel: TypeAlias = ResearchKernel[..., str]


def compose_application_research_kernel(
    backend: ResearchExecutionBackend,
) -> ApplicationResearchKernel:
    """Compose a canonical kernel over the existing execution implementation."""

    registry = getattr(backend, "supervisor_registry", None)
    if not isinstance(registry, TypedRegistry):
        # Lightweight compatibility fakes predate the typed registry. They remain
        # substitutable at the HTTP boundary without becoming production catalogs.
        registry = TypedRegistry()
    return ResearchKernel(
        executor=_DirectExecutionKernelAdapter(backend),
        registry=registry,
    )


def _execution_adapter(
    kernel: ApplicationResearchKernel,
) -> _DirectExecutionKernelAdapter:
    """Return the adapter owned by a composed application kernel."""

    adapter = kernel.executor
    if not isinstance(adapter, _DirectExecutionKernelAdapter):
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
            isinstance(adapter, _DirectExecutionKernelAdapter)
            and adapter.backend is backend
        ):
            return cast(ApplicationResearchKernel, kernel)

    # Existing test and integration clients override the established direct
    # execution dependency, including raw ASGI clients that do not run lifespan.
    return compose_application_research_kernel(backend)


def get_legacy_research_kernel() -> ApplicationResearchKernel:
    """Retain direct-call compatibility outside FastAPI dependency injection."""

    from .direct_execution_service import get_direct_execution_service

    return compose_application_research_kernel(get_direct_execution_service())


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
    "ApplicationResearchKernel",
    "ResearchExecutionBackend",
    "compose_application_research_kernel",
    "get_application_research_kernel",
    "get_kernel_execution_results",
    "get_kernel_execution_status",
    "get_legacy_research_kernel",
    "resume_kernel_execution",
]
