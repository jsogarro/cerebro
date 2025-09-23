"""
Agent Factory for creating and managing research agents.

This module provides a factory pattern for creating agent instances
with appropriate configuration.
"""

import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.citation_agent import CitationAgent
from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.methodology_agent import MethodologyAgent
from src.agents.synthesis_agent import SynthesisAgent

logger = logging.getLogger(__name__)


class AgentFactory:
    """
    Factory class for creating research agents.

    Provides centralized agent creation and management.
    """

    # Registry of available agent types
    _agent_registry: dict[str, type[BaseAgent]] = {
        "literature_review": LiteratureReviewAgent,
        "comparative_analysis": ComparativeAnalysisAgent,
        "methodology": MethodologyAgent,
        "synthesis": SynthesisAgent,
        "citation": CitationAgent,
    }

    @classmethod
    def create_agent(
        cls, agent_type: str, config: dict[str, Any] | None = None
    ) -> BaseAgent:
        """
        Create an agent of the specified type.

        Args:
            agent_type: Type of agent to create
            config: Configuration dictionary with optional:
                - gemini_service: Gemini service instance
                - cache_client: Redis cache client
                - Additional agent-specific settings

        Returns:
            Configured agent instance

        Raises:
            ValueError: If agent_type is not recognized
        """
        if agent_type not in cls._agent_registry:
            raise ValueError(
                f"Unknown agent type: {agent_type}. "
                f"Available types: {list(cls._agent_registry.keys())}"
            )

        config = config or {}
        agent_class = cls._agent_registry[agent_type]

        # Extract common configuration
        gemini_service = config.get("gemini_service")
        cache_client = config.get("cache_client")

        # Initialize MCP integration if configured
        mcp_config = config.get("mcp", {})
        if mcp_config and mcp_config.get("enabled", False):
            mcp_integration = MCPIntegration(
                config=mcp_config,
                enable_fallback=mcp_config.get("enable_fallback", True),
            )
            config["mcp_integration"] = mcp_integration
            logger.info(f"MCP integration enabled for {agent_type} agent")

        # Create agent instance
        agent = agent_class(
            gemini_service=gemini_service, cache_client=cache_client, config=config
        )

        logger.info(f"Created {agent_type} agent")
        return agent

    @classmethod
    def get_all_agents(cls, config: dict[str, Any] | None = None) -> list[BaseAgent]:
        """
        Get instances of all available agents.

        Args:
            config: Configuration to apply to all agents

        Returns:
            List of configured agent instances
        """
        config = config or {}
        agents = []

        for agent_type in cls._agent_registry.keys():
            try:
                agent = cls.create_agent(agent_type, config)
                agents.append(agent)
            except Exception as e:
                logger.warning(f"Failed to create {agent_type} agent: {e}")

        logger.info(f"Created {len(agents)} agents")
        return agents

    @classmethod
    def get_available_agent_types(cls) -> list[str]:
        """
        Get list of available agent types.

        Returns:
            List of agent type identifiers
        """
        return list(cls._agent_registry.keys())

    @classmethod
    def register_agent(cls, agent_type: str, agent_class: type[BaseAgent]):
        """
        Register a new agent type.

        This allows for dynamic registration of custom agents.

        Args:
            agent_type: Identifier for the agent type
            agent_class: Agent class (must inherit from BaseAgent)
        """
        if not issubclass(agent_class, BaseAgent):
            raise ValueError(f"{agent_class} must inherit from BaseAgent")

        cls._agent_registry[agent_type] = agent_class
        logger.info(f"Registered new agent type: {agent_type}")

    @classmethod
    def unregister_agent(cls, agent_type: str):
        """
        Unregister an agent type.

        Args:
            agent_type: Agent type to remove
        """
        if agent_type in cls._agent_registry:
            del cls._agent_registry[agent_type]
            logger.info(f"Unregistered agent type: {agent_type}")

    def get_agent_registry(self) -> dict[str, type[BaseAgent]]:
        """
        Get the current agent registry.

        Returns:
            Dictionary mapping agent types to classes
        """
        return self._agent_registry.copy()

    @classmethod
    def create_agent_with_fallback(
        cls,
        agent_type: str,
        config: dict[str, Any] | None = None,
        fallback_type: str = "literature_review",
    ) -> BaseAgent:
        """
        Create an agent with fallback to a default type if requested type unavailable.

        Args:
            agent_type: Preferred agent type
            config: Agent configuration
            fallback_type: Fallback agent type if preferred is unavailable

        Returns:
            Agent instance (preferred or fallback)
        """
        try:
            return cls.create_agent(agent_type, config)
        except ValueError:
            logger.warning(
                f"Agent type {agent_type} not found, using fallback: {fallback_type}"
            )
            return cls.create_agent(fallback_type, config)
