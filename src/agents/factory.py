"""
Agent Factory for creating and managing research agents.

This module provides a factory pattern for creating agent instances
with appropriate configuration.
"""

from typing import Any

from structlog import get_logger

from src.agents.base import BaseAgent
from src.core.kernel import TypedRegistry
from src.core.kernel.component_keys import AGENT_KEYS

logger = get_logger()


class AgentFactory:
    """
    Factory class for creating research agents.

    Provides centralized agent creation and management.
    """

    # Legacy dynamic registrations are isolated to names outside the built-in
    # application catalog; they cannot override kernel-owned agent tokens.
    _compatibility_extensions: dict[str, type[BaseAgent]] = {}

    def __init__(self, component_registry: TypedRegistry | None = None) -> None:
        """Bind active selection to the application kernel catalog."""

        if component_registry is None:
            from src.api.services.component_catalog import (
                get_default_component_registry,
            )

            component_registry = get_default_component_registry()
        self.component_registry = component_registry

    def resolve_agent(
        self,
        agent_type: str,
        config: dict[str, Any] | None = None,
    ) -> BaseAgent:
        """Create an agent by resolving its implementation token."""

        key = AGENT_KEYS.get(agent_type)
        agent_class: type[BaseAgent] | None
        if key is not None:
            agent_class = self.component_registry.resolve(key)
        else:
            agent_class = self._compatibility_extensions.get(agent_type)
        if agent_class is None:
            raise ValueError(
                f"Unknown agent type: {agent_type}. "
                f"Available types: {self.available_agent_types()}"
            )

        config = config or {}
        config.setdefault("component_registry", self.component_registry)

        # Extract common configuration
        gemini_service = config.get("gemini_service")
        cache_client = config.get("cache_client")

        # Initialize MCP integration if configured
        mcp_config = config.get("mcp", {})
        if mcp_config and mcp_config.get("enabled", False):
            from src.agents.integrations.mcp_integration import MCPIntegration

            mcp_integration = MCPIntegration(
                config=mcp_config,
                enable_fallback=mcp_config.get("enable_fallback", True),
            )
            config["mcp_integration"] = mcp_integration
            logger.info("agent_mcp_integration_enabled", agent_type=agent_type)

        # Create agent instance
        agent = agent_class(
            gemini_service=gemini_service, cache_client=cache_client, config=config
        )

        logger.info("agent_created", agent_type=agent_type)
        return agent

    @classmethod
    def create_agent(
        cls,
        agent_type: str,
        config: dict[str, Any] | None = None,
    ) -> BaseAgent:
        """Retain class-call compatibility over the default typed catalog."""

        return cls().resolve_agent(agent_type, config)

    def available_agent_types(self) -> list[str]:
        """Return agent aliases available through this bound registry."""

        registered = [
            name
            for name, key in AGENT_KEYS.items()
            if key in self.component_registry.keys
        ]
        return [*registered, *self._compatibility_extensions]

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

        factory = cls()
        for agent_type in factory.available_agent_types():
            try:
                agent = factory.resolve_agent(agent_type, config)
                agents.append(agent)
            except Exception as e:
                logger.warning(
                    "agent_create_failed",
                    agent_type=agent_type,
                    error=str(e),
                )

        logger.info("agents_created", agent_count=len(agents))
        return agents

    @classmethod
    def get_available_agent_types(cls) -> list[str]:
        """
        Get list of available agent types.

        Returns:
            List of agent type identifiers
        """
        return cls().available_agent_types()

    @classmethod
    def register_agent(cls, agent_type: str, agent_class: type[BaseAgent]) -> None:
        """
        Register a new agent type.

        This allows for dynamic registration of custom agents.

        Args:
            agent_type: Identifier for the agent type
            agent_class: Agent class (must inherit from BaseAgent)
        """
        if not issubclass(agent_class, BaseAgent):
            raise ValueError(f"{agent_class} must inherit from BaseAgent")
        if agent_type in AGENT_KEYS:
            raise ValueError(
                f"Kernel-owned agent type cannot be overridden: {agent_type}"
            )

        cls._compatibility_extensions[agent_type] = agent_class
        logger.info("agent_type_registered", agent_type=agent_type)

    @classmethod
    def unregister_agent(cls, agent_type: str) -> None:
        """
        Unregister an agent type.

        Args:
            agent_type: Agent type to remove
        """
        if agent_type in cls._compatibility_extensions:
            del cls._compatibility_extensions[agent_type]
            logger.info("agent_type_unregistered", agent_type=agent_type)

    def get_agent_registry(self) -> dict[str, type[BaseAgent]]:
        """
        Get the current agent registry.

        Returns:
            Dictionary mapping agent types to classes
        """
        return {
            name: self.component_registry.resolve(key)
            for name, key in AGENT_KEYS.items()
            if key in self.component_registry.keys
        } | self._compatibility_extensions.copy()

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
            return cls().resolve_agent(agent_type, config)
        except ValueError:
            logger.warning(
                "agent_type_fallback_used",
                agent_type=agent_type,
                fallback_type=fallback_type,
            )
            return cls().resolve_agent(fallback_type, config)
