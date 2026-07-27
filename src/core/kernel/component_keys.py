"""Shared capability tokens for the application component catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.models.agent_api_models import AgentType

from .registry import RegistryKey, RegistryNamespace

if TYPE_CHECKING:
    from src.agents.base import BaseAgent
    from src.agents.supervisors.base_supervisor import BaseSupervisor
    from src.ai_brain.providers.base_provider import BaseProvider


AGENT_KEYS: dict[str, RegistryKey[type[BaseAgent]]] = {
    name: RegistryKey(RegistryNamespace.AGENT, name.replace("_", "-"))
    for name in (
        "literature_review",
        "comparative_analysis",
        "methodology",
        "synthesis",
        "citation",
        "financial_calculator",
        "content_planning",
        "drafting",
        "editing",
        "optimization",
        "data_analysis",
        "statistical_modeling",
        "insight_synthesis",
        "financial_analysis",
        "valuation",
        "risk_assessment",
        "verification",
    )
}

API_AGENT_KEYS: dict[AgentType, RegistryKey[type[BaseAgent]]] = {
    agent_type: AGENT_KEYS[agent_type.value.replace("-", "_")]
    for agent_type in AgentType
}

SUPERVISOR_KEYS: dict[str, RegistryKey[type[BaseSupervisor]]] = {
    name: RegistryKey(RegistryNamespace.SUPERVISOR, name)
    for name in ("analytics", "content", "finance", "research")
}

DOMAIN_KEYS: dict[str, RegistryKey[str]] = {
    name: RegistryKey(RegistryNamespace.DOMAIN, name)
    for name in (
        "analytics",
        "content",
        "finance",
        "general",
        "multimodal",
        "research",
        "service",
    )
}

PROVIDER_KEYS: dict[str, RegistryKey[type[BaseProvider]]] = {
    name: RegistryKey(RegistryNamespace.PROVIDER, name)
    for name in ("deepseek", "gemini", "llama", "openrouter")
}

ROUTED_RESEARCH_WORKFLOW_KEY = RegistryKey[Any](
    RegistryNamespace.WORKFLOW,
    "routed-research",
)
DIRECT_AGENT_WORKFLOW_KEY = RegistryKey[Any](
    RegistryNamespace.WORKFLOW,
    "direct-agent",
)
AGENT_CHAIN_WORKFLOW_KEY = RegistryKey[Any](
    RegistryNamespace.WORKFLOW,
    "agent-chain",
)
AGENT_MIXTURE_WORKFLOW_KEY = RegistryKey[Any](
    RegistryNamespace.WORKFLOW,
    "agent-mixture",
)
COLLABORATION_MODE_WORKFLOW_KEY = RegistryKey[Any](
    RegistryNamespace.WORKFLOW,
    "collaboration-mode",
)


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
]
