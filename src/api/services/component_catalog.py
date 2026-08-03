"""Canonical application component tokens and immutable catalog composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, Protocol

from src.agents.analytics_agents import (
    DataAnalysisAgent,
    InsightSynthesisAgent,
    StatisticalModelingAgent,
)
from src.agents.base import BaseAgent
from src.agents.citation_agent import CitationAgent
from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.content_agents import (
    ContentPlanningAgent,
    DraftingAgent,
    EditingAgent,
    OptimizationAgent,
)
from src.agents.finance_agents import (
    FinancialAnalysisAgent,
    RiskAssessmentAgent,
    ValuationAgent,
)
from src.agents.financial_calculator_agent import FinancialCalculatorAgent
from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.methodology_agent import MethodologyAgent
from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor
from src.agents.supervisors.base_supervisor import BaseSupervisor
from src.agents.supervisors.content_supervisor import ContentSupervisor
from src.agents.supervisors.finance_supervisor import FinanceSupervisor
from src.agents.supervisors.research_supervisor import ResearchSupervisor
from src.agents.synthesis_agent import SynthesisAgent
from src.agents.verification_agent import VerificationAgent
from src.ai_brain.providers.base_provider import BaseProvider
from src.ai_brain.providers.deepseek_provider import DeepSeekProvider
from src.ai_brain.providers.gemini_provider import GeminiProvider
from src.ai_brain.providers.llama_provider import LlamaProvider
from src.ai_brain.providers.openrouter_provider import OpenRouterProvider
from src.ai_brain.router.routing_types import CollaborationMode
from src.core.kernel import (
    RegistryEntry,
    TypedRegistry,
)
from src.core.kernel.component_keys import (
    AGENT_CHAIN_WORKFLOW_KEY,
    AGENT_KEYS,
    AGENT_MIXTURE_WORKFLOW_KEY,
    API_AGENT_KEYS,
    COLLABORATION_MODE_WORKFLOW_KEY,
    DIRECT_AGENT_WORKFLOW_KEY,
    DOMAIN_KEYS,
    PROVIDER_KEYS,
    ROUTED_RESEARCH_WORKFLOW_KEY,
    SUPERVISOR_KEYS,
)
from src.models.agent_api_models import (
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentType,
    ChainOfAgentsRequest,
    ChainOfAgentsResponse,
    MixtureOfAgentsRequest,
    MixtureOfAgentsResponse,
)
from src.models.execution_authority import ExecutionAuthorityReference
from src.models.research_project import ResearchProject


class ResearchWorkflowBackend(Protocol):
    """Call shape implemented by the existing routed-research backend."""

    async def start_research_execution(
        self,
        project: ResearchProject,
        context: dict[str, Any] | None = None,
        *,
        authority_reference: ExecutionAuthorityReference | None = None,
    ) -> str: ...


class AgentWorkflowBackend(Protocol):
    """Call shapes implemented by the existing direct-agent backend."""

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


ResearchWorkflow = Callable[
    [
        ResearchWorkflowBackend,
        ResearchProject,
        dict[str, Any] | None,
        ExecutionAuthorityReference | None,
    ],
    Awaitable[str],
]
DirectAgentWorkflow = Callable[
    [AgentWorkflowBackend, AgentType, AgentExecutionRequest],
    Awaitable[AgentExecutionResponse],
]
AgentChainWorkflow = Callable[
    [AgentWorkflowBackend, ChainOfAgentsRequest],
    Awaitable[ChainOfAgentsResponse],
]
AgentMixtureWorkflow = Callable[
    [AgentWorkflowBackend, MixtureOfAgentsRequest],
    Awaitable[MixtureOfAgentsResponse],
]
CollaborationModeResolver = Callable[[CollaborationMode], str]


async def execute_routed_research(
    backend: ResearchWorkflowBackend,
    project: ResearchProject,
    context: dict[str, Any] | None,
    authority_reference: ExecutionAuthorityReference | None,
) -> str:
    """Preserve the routed-research workflow call shape."""

    if authority_reference is None:
        return await backend.start_research_execution(project, context)
    return await backend.start_research_execution(
        project,
        context,
        authority_reference=authority_reference,
    )


async def execute_direct_agent(
    backend: AgentWorkflowBackend,
    agent_type: AgentType,
    request: AgentExecutionRequest,
) -> AgentExecutionResponse:
    """Preserve the direct-agent workflow call shape."""

    return await backend.execute_single_agent(agent_type, request)


async def execute_agent_chain(
    backend: AgentWorkflowBackend,
    request: ChainOfAgentsRequest,
) -> ChainOfAgentsResponse:
    """Preserve the sequential agent-chain workflow call shape."""

    return await backend.execute_chain_of_agents(request)


async def execute_agent_mixture(
    backend: AgentWorkflowBackend,
    request: MixtureOfAgentsRequest,
) -> MixtureOfAgentsResponse:
    """Preserve the bounded parallel agent-mixture workflow call shape."""

    return await backend.execute_mixture_of_agents(request)


def resolve_collaboration_mode(mode: CollaborationMode) -> str:
    """Translate current MASR collaboration policy without changing decisions."""

    return {
        CollaborationMode.DIRECT: "sequential",
        CollaborationMode.PARALLEL: "parallel",
        CollaborationMode.HIERARCHICAL: "hybrid",
        CollaborationMode.DEBATE: "adaptive",
        CollaborationMode.ENSEMBLE: "parallel",
    }.get(mode, "parallel")


_AGENT_COMPONENTS: dict[str, type[BaseAgent]] = {
    "literature_review": LiteratureReviewAgent,
    "comparative_analysis": ComparativeAnalysisAgent,
    "methodology": MethodologyAgent,
    "synthesis": SynthesisAgent,
    "citation": CitationAgent,
    "financial_calculator": FinancialCalculatorAgent,
    "content_planning": ContentPlanningAgent,
    "drafting": DraftingAgent,
    "editing": EditingAgent,
    "optimization": OptimizationAgent,
    "data_analysis": DataAnalysisAgent,
    "statistical_modeling": StatisticalModelingAgent,
    "insight_synthesis": InsightSynthesisAgent,
    "financial_analysis": FinancialAnalysisAgent,
    "valuation": ValuationAgent,
    "risk_assessment": RiskAssessmentAgent,
    "verification": VerificationAgent,
}

_SUPERVISOR_COMPONENTS: dict[str, type[BaseSupervisor]] = {
    "analytics": AnalyticsSupervisor,
    "content": ContentSupervisor,
    "finance": FinanceSupervisor,
    "research": ResearchSupervisor,
}

_DOMAIN_SUPERVISOR_NAMES = {
    "analytics": "analytics",
    "content": "content",
    "finance": "research",
    "general": "research",
    "multimodal": "content",
    "research": "research",
    "service": "service",
}

_PROVIDER_COMPONENTS: dict[str, type[BaseProvider]] = {
    "deepseek": DeepSeekProvider,
    "gemini": GeminiProvider,
    "llama": LlamaProvider,
    "openrouter": OpenRouterProvider,
}


def build_application_component_registry() -> TypedRegistry:
    """Build one immutable catalog for a single application lifespan."""

    entries: list[RegistryEntry[Any]] = [
        RegistryEntry(AGENT_KEYS[name], component)
        for name, component in _AGENT_COMPONENTS.items()
    ]
    entries.extend(
        RegistryEntry(SUPERVISOR_KEYS[name], component)
        for name, component in _SUPERVISOR_COMPONENTS.items()
    )
    entries.extend(
        RegistryEntry(DOMAIN_KEYS[name], supervisor_name)
        for name, supervisor_name in _DOMAIN_SUPERVISOR_NAMES.items()
    )
    entries.extend(
        RegistryEntry(PROVIDER_KEYS[name], component)
        for name, component in _PROVIDER_COMPONENTS.items()
    )
    entries.extend(
        (
            RegistryEntry(ROUTED_RESEARCH_WORKFLOW_KEY, execute_routed_research),
            RegistryEntry(DIRECT_AGENT_WORKFLOW_KEY, execute_direct_agent),
            RegistryEntry(AGENT_CHAIN_WORKFLOW_KEY, execute_agent_chain),
            RegistryEntry(AGENT_MIXTURE_WORKFLOW_KEY, execute_agent_mixture),
            RegistryEntry(
                COLLABORATION_MODE_WORKFLOW_KEY,
                resolve_collaboration_mode,
            ),
        )
    )
    return TypedRegistry(entries)


@lru_cache(maxsize=1)
def get_default_component_registry() -> TypedRegistry:
    """Return the immutable compatibility catalog outside app lifespan."""

    return build_application_component_registry()


__all__ = [
    "AGENT_CHAIN_WORKFLOW_KEY",
    "AGENT_KEYS",
    "AGENT_MIXTURE_WORKFLOW_KEY",
    "API_AGENT_KEYS",
    "COLLABORATION_MODE_WORKFLOW_KEY",
    "DIRECT_AGENT_WORKFLOW_KEY",
    "DOMAIN_KEYS",
    "PROVIDER_KEYS",
    "ROUTED_RESEARCH_WORKFLOW_KEY",
    "SUPERVISOR_KEYS",
    "build_application_component_registry",
    "get_default_component_registry",
]
